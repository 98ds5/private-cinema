"""
播放控制服务 — 双引擎架构

支持两种播放器, 在 config.json 的 player.engine 切换:

  1. MPV (默认, 推荐)
     JSON IPC 读取播放进度, 支持续播定位。
     Windows 上用命名管道通信 (ctypes 直接调用 kernel32 API, 无需额外依赖)。

  2. VividPlayer (微软商店版)
     通过 vividplayer://openfile 协议启动, 纯播放, 不做任何进度追踪。

设计:
  PlayerService  → 统一外观, 自动转发到当前引擎
  _MpvEngine     → MPV + JSON IPC (全功能)
  _VividPlayerEngine → 协议启动 + 时间估算 (简化版)
"""
import json
import os
import platform
import subprocess
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from PySide6.QtCore import QObject, Signal, QTimer

from app.database import get_session
from app.models.tables import Media, Episode, MediaFile, Season

# ========================================================================
# Windows 命名管道 API (ctypes) — 用于 MPV JSON IPC 通信
# ========================================================================
_HAVE_NAMED_PIPE = False
_kernel32 = None
if platform.system() == "Windows":
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    # CTypes 签名
    _kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,           # lpFileName
        wintypes.DWORD,             # dwDesiredAccess
        wintypes.DWORD,             # dwShareMode
        ctypes.c_void_p,            # lpSecurityAttributes
        wintypes.DWORD,             # dwCreationDisposition
        wintypes.DWORD,             # dwFlagsAndAttributes
        wintypes.HANDLE,            # hTemplateFile
    ]
    _kernel32.CreateFileW.restype = wintypes.HANDLE

    _kernel32.WriteFile.argtypes = [
        wintypes.HANDLE,            # hFile
        ctypes.c_void_p,            # lpBuffer
        wintypes.DWORD,             # nNumberOfBytesToWrite
        ctypes.POINTER(wintypes.DWORD),  # lpNumberOfBytesWritten
        ctypes.c_void_p,            # lpOverlapped
    ]
    _kernel32.WriteFile.restype = wintypes.BOOL

    _kernel32.ReadFile.argtypes = [
        wintypes.HANDLE,            # hFile
        ctypes.c_void_p,            # lpBuffer
        wintypes.DWORD,             # nNumberOfBytesToRead
        ctypes.POINTER(wintypes.DWORD),  # lpNumberOfBytesRead
        ctypes.c_void_p,            # lpOverlapped
    ]
    _kernel32.ReadFile.restype = wintypes.BOOL

    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL

    _kernel32.GetLastError.restype = wintypes.DWORD

    _kernel32.SetNamedPipeHandleState.argtypes = [
        wintypes.HANDLE,            # hNamedPipe
        ctypes.POINTER(wintypes.DWORD),  # lpMode
        ctypes.c_void_p,            # lpMaxCollectionCount
        ctypes.c_void_p,            # lpCollectDataTimeout
    ]
    _kernel32.SetNamedPipeHandleState.restype = wintypes.BOOL

    _HAVE_NAMED_PIPE = True

    # 常量
    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_NORMAL = 0x80
    PIPE_READ_MODE = 0x0001  # PIPE_READMODE_MESSAGE


# ========================================================================
# 工具函数
# ========================================================================
def _save_progress(media_id: int, position: int, duration: int,
                   episode_id: Optional[int] = None):
    """
    保存播放进度到数据库 (双引擎通用)。

    电影: 存 media 表
    动漫: 存 episodes 表 (media 整体状态由各 episode 聚合)
    """
    if not media_id:
        return
    with get_session() as s:
        now = datetime.now()
        status = "watched" if duration > 0 and position >= duration * 0.95 else "watching"

        if episode_id:
            ep = s.query(Episode).filter(Episode.id == episode_id).first()
            if ep:
                ep.status = status
                ep.watched_position = position
                ep.last_watched_at = now

                # 聚合回写 media 表。
                # 首页「已观看/观看中」统计与「最近观看」列表都只读 media,
                # 不回写的话看完动漫也永远显示未观看、且不进最近观看。
                media = s.query(Media).filter(Media.id == media_id).first()
                if media:
                    # Episode 没有 media_id, 关系是 Episode → Season → Media,
                    # 必须经 Season 关联查询。
                    eps = (
                        s.query(Episode)
                        .join(Season, Episode.season_id == Season.id)
                        .filter(Season.media_id == media_id)
                        .all()
                    )          # 自动 flush, 含刚改的这一集
                    done = sum(1 for e in eps if e.status == "watched")
                    media.status = (
                        "watched" if eps and done == len(eps) else "watching"
                    )
                    media.watched_position = position
                    media.watched_duration = duration
                    media.last_watched_at = now
        else:
            media = s.query(Media).filter(Media.id == media_id).first()
            if media:
                media.status = status
                media.watched_position = position
                media.watched_duration = duration
                media.last_watched_at = now
        s.commit()


# ========================================================================
# 引擎: MPV (JSON IPC 命名管道)
# ========================================================================
class _MpvEngine(QObject):
    """MPV 播放引擎 — 通过 JSON IPC 读取进度"""

    playback_started = Signal(int)
    playback_position = Signal(int, int)
    playback_finished = Signal(int)
    playback_error = Signal(str)

    def __init__(self, config: dict, mpv_path: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.mpv_path = mpv_path or "mpv"
        self.interval = config.get("player", {}).get("progress_interval", 10)
        self.hw_decode = config.get("player", {}).get("hw_decode", "auto")

        self._process: Optional[subprocess.Popen] = None
        self._pipe_handle: Optional[int] = None  # Windows HANDLE
        self._media_id: Optional[int] = None
        self._episode_id: Optional[int] = None
        self._position = 0
        self._duration = 0
        self._playing = False
        self._pipe_name = ""

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)

    @property
    def is_playing(self) -> bool:
        return self._playing

    def play(self, file_path: str, media_id: int,
             episode_id: int = None, start_pos: int = 0):
        if self._playing:
            self.stop()

        self._media_id = media_id
        self._episode_id = episode_id
        self._position = start_pos
        self._duration = 0

        # 命名管道路径 (Windows)
        self._pipe_name = rf"\\.\pipe\mpv-pc-{media_id}"

        cmd = [
            self.mpv_path,
            f"--input-ipc-server={self._pipe_name}",
            f"--hwdec={self.hw_decode}",
            "--keep-open=yes",
            "--no-border",
            "--geometry=50%+10%+10%",  # 居中偏上
        ]
        if start_pos > 0:
            cmd.append(f"--start={start_pos}")
        cmd.append(file_path)

        try:
            self._process = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            self._playing = True
            self.playback_started.emit(media_id)

            # 后台线程: 连接命名管道
            threading.Thread(target=self._connect, daemon=True).start()

        except FileNotFoundError:
            self.playback_error.emit(f"未找到 MPV: {self.mpv_path}")

    def stop(self):
        if not self._playing:
            return
        self._playing = False
        self._timer.stop()

        # 发 quit 命令
        if self._pipe_handle:
            try:
                self._send_raw(json.dumps({"command": ["quit"]}) + "\n")
            except Exception:
                pass
            self._close_pipe()

        # 兜底: 等进程退出
        if self._process:
            try:
                self._process.wait(timeout=3)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None

        self._save()

    # ---- 命名管道通信 ----

    def _connect(self):
        """后台线程: 等待 MPV 创建管道, 然后连接"""
        if platform.system() != "Windows" or not _HAVE_NAMED_PIPE:
            self.playback_error.emit("MPV JSON IPC 当前仅支持 Windows (命名管道)")
            return

        # 等 MPV 创建管道 (最多 3 秒)
        for _ in range(30):
            if not self._playing:
                return
            time.sleep(0.1)

        handle = _kernel32.CreateFileW(
            self._pipe_name,
            GENERIC_READ | GENERIC_WRITE,
            0,               # 不共享
            None,            # 默认安全属性
            OPEN_EXISTING,
            FILE_ATTRIBUTE_NORMAL,
            None,
        )
        if handle == wintypes.HANDLE(-1).value or not handle:
            err = _kernel32.GetLastError()
            self.playback_error.emit(f"连接 MPV 命名管道失败 (error={err})")
            return

        self._pipe_handle = handle

        # 切换到消息读取模式
        PIPE_READ_MODE = 0x0001
        c_mode = wintypes.DWORD(PIPE_READ_MODE)
        _kernel32.SetNamedPipeHandleState(
            handle, ctypes.byref(c_mode), None, None
        )

        # 启动定时轮询
        self._timer.start(self.interval * 1000)

    def _send_raw(self, data: str) -> bool:
        """发送原始数据到命名管道"""
        if not self._pipe_handle:
            return False
        buf = data.encode("utf-8")
        written = wintypes.DWORD(0)
        ok = _kernel32.WriteFile(
            self._pipe_handle,
            buf,
            len(buf),
            ctypes.byref(written),
            None,
        )
        return bool(ok)

    def _read_response(self) -> Optional[str]:
        """从命名管道读取一行响应"""
        if not self._pipe_handle:
            return None
        buf = ctypes.create_string_buffer(4096)
        read = wintypes.DWORD(0)
        ok = _kernel32.ReadFile(
            self._pipe_handle,
            buf,
            ctypes.sizeof(buf),
            ctypes.byref(read),
            None,
        )
        if ok and read.value > 0:
            return buf.raw[:read.value].decode("utf-8", errors="replace").strip()
        return None

    def _send_command(self, command: list) -> Optional[dict]:
        """发送 JSON IPC 命令并解析响应"""
        payload = json.dumps({"command": command}) + "\n"
        if not self._send_raw(payload):
            return None
        resp = self._read_response()
        if resp:
            try:
                return json.loads(resp)
            except json.JSONDecodeError:
                pass
        return None

    def _close_pipe(self):
        if self._pipe_handle:
            _kernel32.CloseHandle(self._pipe_handle)
            self._pipe_handle = None

    # ---- 轮询与保存 ----

    def _poll(self):
        """定时器回调: 读取 time-pos 和 duration"""
        if not self._playing or not self._pipe_handle:
            return

        # 检查进程是否还活着
        if self._process and self._process.poll() is not None:
            # MPV 已退出
            self._timer.stop()
            self._playing = False
            self._save()
            self.playback_finished.emit(self._media_id)
            self._close_pipe()
            return

        # 查询播放位置
        resp = self._send_command(["get_property", "time-pos"])
        if resp and "data" in resp:
            self._position = int(float(resp["data"]))

        # 查询总时长 (只查一次)
        if self._duration == 0:
            resp2 = self._send_command(["get_property", "duration"])
            if resp2 and "data" in resp2:
                self._duration = int(float(resp2["data"]))

        self.playback_position.emit(self._position, self._duration)

        # 每轮询也落库一次 (防止崩溃丢进度)
        self._save()

    def _save(self):
        """保存当前进度到数据库"""
        _save_progress(
            media_id=self._media_id,
            position=self._position,
            duration=self._duration,
            episode_id=self._episode_id,
        )


# ========================================================================
# 引擎: VividPlayer (纯启动, 无进度追踪)
# ========================================================================
def _vividplayer_url(file_path: str) -> str:
    """
    构造 VividPlayer 协议 URL。

    形式经实测确定: vividplayer://openfile?path=<百分号编码路径>
    路径必须整体编码 (safe=""), 否则盘符冒号、反斜杠、空格、中文都会让
    对端解析错; 默认 quote(safe="/") 不编码反斜杠以外的分隔语义, 不够严格。
    """
    return "vividplayer://openfile?path=" + urllib.parse.quote(
        file_path, safe=""
    )


class _VividPlayerEngine(QObject):
    """VividPlayer 播放引擎 — 纯启动, 不追踪进度, 不支持续播"""

    playback_started = Signal(int)
    playback_position = Signal(int, int)
    playback_finished = Signal(int)
    playback_error = Signal(str)

    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self._media_id: Optional[int] = None
        self._playing = False
        self._process: Optional[subprocess.Popen] = None

    @property
    def is_playing(self) -> bool:
        return self._playing

    def play(self, file_path: str, media_id: int,
             episode_id: int = None, start_pos: int = 0):
        """启动 VividPlayer 播放文件; start_pos 忽略 (不支持续播)"""
        if self._playing:
            self.stop()
        self._media_id = media_id

        launched = self._launch(file_path)
        if not launched:
            self.playback_error.emit("无法启动 VividPlayer, 请检查安装或换用 MPV 引擎")
            return

        self._playing = True
        self.playback_started.emit(media_id)

    def _launch(self, file_path: str) -> bool:
        """
        通过协议激活 VividPlayer (UWP/Store 应用)。

        实测结论 (VividPlayer 1.1.12.0, 读包清单 + 实跑验证):
          清单声明了 vividplayer / vividshot 两个协议, 以及 40 种视频扩展名的
          文件关联。可用形式是:
              vividplayer://openfile?path=<百分号编码的完整路径>
          形式 "vividplayer://<路径>" (不带 openfile?path=) 拉不起进程。

        三个必须注意的坑 (都真实踩过):
          1. ShellExecuteW 返回 HINSTANCE, 是指针宽度。不设 restype 时 ctypes
             按 32 位 int 截断, "> 32 即成功" 的判断不可靠 —— 会让失败被当成
             成功, 从而跳过后续处理、也不报错, 表现为"点了没反应"。
          2. 路径必须整体百分号编码 (含盘符冒号、反斜杠、空格、中文),
             用 quote(safe="") 而不是默认的 safe="/"。
          3. 不再静默回退到 os.startfile(): 用户系统里 .mkv 的默认程序可能是
             别的播放器 (实测是 MPC-HC), 静默回退会让人误以为 VividPlayer
             生效了。失败就明确报错。
        """
        if platform.system() != "Windows":
            return False

        try:
            import ctypes
            from ctypes import wintypes
        except ImportError:
            return False

        url = _vividplayer_url(file_path)

        try:
            shell32 = ctypes.windll.shell32
            shell32.ShellExecuteW.restype = wintypes.HINSTANCE
            shell32.ShellExecuteW.argtypes = [
                wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
                wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int,
            ]
            ret = shell32.ShellExecuteW(None, "open", url, None, None, 1)
        except Exception:
            return False

        # 返回值 <= 32 表示失败, 其值是 SE_ERR_* 错误码
        code = int(ret) if ret is not None else 0
        if code <= 32:
            self.playback_error.emit(
                f"VividPlayer 协议激活失败 (SE_ERR 代码 {code})"
            )
            return False
        return True

    def stop(self):
        if not self._playing:
            return
        self._playing = False
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=2)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None
        self.playback_finished.emit(self._media_id)


# ========================================================================
# 统一外观: PlayerService
# ========================================================================
class PlayerService(QObject):
    """
    播放控制服务 — 统一外观

    根据 config['player']['engine'] 自动选择引擎:
      "mpv"          → MPV + JSON IPC (默认, 推荐)
      "vividplayer"  → VividPlayer (纯启动, 无进度追踪)
    """

    playback_started = Signal(int)
    playback_position = Signal(int, int)
    playback_finished = Signal(int)
    playback_error = Signal(str)

    def __init__(self, config: dict, mpv_path: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.config = config
        self.mpv_path = mpv_path          # 重建引擎时要复用, 必须存下来
        self._build_engine(self.engine_type)

    def _build_engine(self, engine_type: str):
        """构建引擎并转发其信号 (构造与运行时切换共用同一套逻辑)"""
        if engine_type == "vividplayer":
            self._engine = _VividPlayerEngine(self.config, self)
        else:
            if engine_type != "mpv":
                self.playback_error.emit(f"未知的播放引擎: {engine_type}, 回退 MPV")
            self._engine = _MpvEngine(self.config, self.mpv_path, self)

        self._engine.playback_started.connect(self.playback_started)
        self._engine.playback_position.connect(self.playback_position)
        self._engine.playback_finished.connect(self.playback_finished)
        self._engine.playback_error.connect(self.playback_error)

    def set_engine(self, engine_type: str) -> bool:
        """
        运行时切换播放引擎。

        原先引擎只在 __init__ 里选定一次, 设置页切换后只写了配置文件,
        必须重启 app 才生效 —— 用户想从 VividPlayer 切到 MPV 测进度时
        会以为设置坏了。
        """
        if engine_type not in ("mpv", "vividplayer"):
            self.playback_error.emit(f"未知的播放引擎: {engine_type}")
            return False
        if engine_type == self.engine_type and hasattr(self, "_engine"):
            return True                    # 没变化, 不重建

        try:
            self.stop()                    # 先停掉旧引擎, 避免留下野进程
        except Exception:
            pass

        self.config.setdefault("player", {})["engine"] = engine_type
        self._build_engine(engine_type)
        return True

    @property
    def is_playing(self) -> bool:
        return self._engine.is_playing

    @property
    def engine_type(self) -> str:
        return self.config.get("player", {}).get("engine", "mpv")

    def play(self, file_path: str, media_id: int,
             episode_id: int = None, start_pos: int = 0):
        self._engine.play(file_path, media_id, episode_id, start_pos)

    def stop(self):
        self._engine.stop()