"""
播放控制服务: 双引擎, 由 config 的 player.engine 切换。
mpv          : 命名管道 JSON IPC 读进度, 支持续播 (默认)
vividplayer  : vividplayer://openfile 协议启动, 纯播放, 不追踪进度
"""
import json
import os
import platform
import subprocess
import threading
import time
import urllib.parse
import uuid
from datetime import datetime
from typing import Optional, Tuple

from PySide6.QtCore import QObject, Signal

from app.database import get_session
from app.models.tables import Media, Episode, MediaFile, Season

# ========================================================================
# Windows 命名管道 API (ctypes), 供 MPV 的 JSON IPC 使用
# ========================================================================
_HAVE_NAMED_PIPE = False
_kernel32 = None
if platform.system() == "Windows":
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    # ctypes 函数签名
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

    # PeekNamedPipe: 先问管道里有多少可读字节再决定读多少,
    # 否则只能阻塞式 ReadFile, 管道空时会把调用线程挂死
    _kernel32.PeekNamedPipe.argtypes = [
        wintypes.HANDLE,            # hNamedPipe
        ctypes.c_void_p,            # lpBuffer
        wintypes.DWORD,             # nBufferSize
        ctypes.POINTER(wintypes.DWORD),  # lpBytesRead
        ctypes.POINTER(wintypes.DWORD),  # lpTotalBytesAvail
        ctypes.POINTER(wintypes.DWORD),  # lpBytesLeftThisMessage
    ]
    _kernel32.PeekNamedPipe.restype = wintypes.BOOL

    _HAVE_NAMED_PIPE = True

    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_NORMAL = 0x80
    _INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value


# ========================================================================
# 工具函数
# ========================================================================
def _save_progress(media_id: int, position: int, duration: int,
                   episode_id: Optional[int] = None):
    """
    保存播放进度到数据库 (双引擎通用)。
    电影存 media 表; 动漫存 episodes 表, 再把整体状态聚合回 media。
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

                # 聚合回写 media 表: 首页统计和「最近观看」都只读 media
                media = s.query(Media).filter(Media.id == media_id).first()
                if media:
                    # Episode 没有 media_id, 要经 Season 关联才查得到同一部的各集
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
# MPV JSON IPC 的字节流解析 (纯函数, 便于单测)
# ========================================================================
def _iter_json_lines(data: bytes) -> Tuple[list, bytes]:
    """
    按行把字节流切成 JSON 对象, 返回 (对象列表, 没凑成整行的剩余字节)。
    剩余字节必须由调用方存回缓冲: mpv 的一行可能被拆到两次 ReadFile 里。
    坏行直接跳过, 一行乱码不该毁掉后面的正常数据。
    """
    objs = []
    while b"\n" in data:
        line, data = data.split(b"\n", 1)
        line = line.strip()
        if not line:
            continue
        try:
            objs.append(json.loads(line.decode("utf-8", "replace")))
        except json.JSONDecodeError:
            continue
    return objs, data


def _pick_reply(objects: list, request_id: int) -> Optional[dict]:
    """
    从混杂的对象流里挑出本次命令的回复, 丢弃 mpv 主动推的事件行。
    判据是 request_id: 事件行没有这个字段, 命令回复一定有。
    """
    for o in objects:
        if not isinstance(o, dict) or "event" in o:
            continue
        if o.get("request_id") == request_id:
            return o
    return None


class _PipeReader:
    """
    命名管道上按行读写的 JSON 通道 (后台 IPC 线程专用)。

    mpv 会主动推事件, 和命令回复混在同一次读取里, 所以只能逐行解析 +
    按 request_id 挑回复, 不能整块 json.loads。
    一律先 PeekNamedPipe 再读: 同步管道没有数据时直接 ReadFile 会挂死线程。
    """

    def __init__(self, handle: int):
        self._h = handle
        self._buf = b""

    def send(self, obj: dict) -> bool:
        payload = (json.dumps(obj) + "\n").encode("utf-8")
        written = wintypes.DWORD(0)
        return bool(_kernel32.WriteFile(
            self._h, payload, len(payload), ctypes.byref(written), None
        ))

    def pump(self) -> Tuple[list, bool]:
        """非阻塞读走当前所有可读字节, 返回 (解析出的 JSON 对象, 管道是否还活着)"""
        objs: list = []
        while True:
            avail = wintypes.DWORD(0)
            if not _kernel32.PeekNamedPipe(
                    self._h, None, 0, None, ctypes.byref(avail), None):
                return objs, False
            if avail.value <= 0:
                return objs, True
            chunk = ctypes.create_string_buffer(avail.value)
            got = wintypes.DWORD(0)
            if not _kernel32.ReadFile(
                    self._h, chunk, avail.value, ctypes.byref(got), None):
                return objs, False
            if got.value <= 0:
                return objs, True
            self._buf += chunk.raw[:got.value]
            parsed, self._buf = _iter_json_lines(self._buf)
            objs.extend(parsed)

    def await_reply(self, request_id: int, timeout: float = 2.0):
        """读到与 request_id 匹配的回复为止; 超时或断管返回 None"""
        deadline = time.monotonic() + timeout
        while True:
            objs, alive = self.pump()
            reply = _pick_reply(objs, request_id)
            if reply is not None:
                if reply.get("error") not in (None, "success"):
                    return None
                return reply.get("data")
            if not alive or time.monotonic() >= deadline:
                return None
            time.sleep(0.02)

    def close(self):
        if self._h:
            _kernel32.CloseHandle(self._h)
            self._h = None


# ========================================================================
# 引擎: MPV (JSON IPC 命名管道)
# ========================================================================
class _MpvEngine(QObject):
    """
    MPV 播放引擎: 通过 JSON IPC 读取进度。

    线程模型: 主线程 play()/stop()、全部信号槽和写库;
    后台 _ipc_loop() 连管道轮询进度, 只发信号。
    这么分有两个硬约束: QTimer 不能在非 Qt 线程里 start, SQLite 连接也不能跨线程复用。
    """

    playback_started = Signal(int)
    playback_position = Signal(int, int)
    playback_finished = Signal(int)
    playback_error = Signal(str)

    # 后台 IPC 线程 -> 主线程的私有通道 (跨线程自动走 queued connection)
    _progress_ready = Signal(int, int)
    _ipc_failed = Signal(str)
    _process_exited = Signal()

    def __init__(self, config: dict, mpv_path: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.mpv_path = mpv_path or "mpv"
        player_cfg = config.get("player", {}) or {}

        # 轮询间隔(秒)。0 或非法值一律退回 10, 否则后台线程会变成忙等。
        try:
            self.interval = float(player_cfg.get("progress_interval", 10))
        except (TypeError, ValueError):
            self.interval = 10.0
        if self.interval <= 0:
            self.interval = 10.0
        self.hw_decode = player_cfg.get("hw_decode", "auto")

        self._process: Optional[subprocess.Popen] = None
        self._media_id: Optional[int] = None
        self._episode_id: Optional[int] = None
        self._position = 0
        self._duration = 0
        self._playing = False
        self._pipe_name = ""
        self._connect_error = 0
        self._next_request_id = 0
        self._ipc_thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()

        self._progress_ready.connect(self._on_progress)
        self._ipc_failed.connect(self._on_ipc_failed)
        self._process_exited.connect(self._on_process_exited)

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
        self._next_request_id = 0

        # 管道名每次播放都要唯一: 上一轮残留的 mpv 还占着同名管道的话,
        # CreateFileW 会连到别的 mpv 上, 读回来的是别的片子的进度
        self._pipe_name = rf"\\.\pipe\mpv-pc-{os.getpid()}-{uuid.uuid4().hex[:8]}"

        cmd = [
            self.mpv_path,
            f"--input-ipc-server={self._pipe_name}",
            f"--hwdec={self.hw_decode}",
            "--keep-open=yes",
            "--no-border",
            "--geometry=50%+10%+10%",  # 居中偏上
            # 关掉 mpv 自己的观看位置记忆, 让本 app 的数据库是唯一进度来源。
            # 命令行优先级高于 mpv.conf, 能压住用户全局的 save-position-on-quit。
            "--no-resume-playback",
            "--save-position-on-quit=no",
        ]
        if start_pos > 0:
            cmd.append(f"--start={start_pos}")
        cmd.append(file_path)

        try:
            self._process = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except (FileNotFoundError, OSError) as e:
            # 别只捕 FileNotFoundError: 路径指向目录或 exe 损坏时抛的是别的 OSError
            self._process = None
            self.playback_error.emit(f"无法启动 MPV: {self.mpv_path} ({e})")
            return

        self._playing = True
        self._stop_evt.clear()
        self.playback_started.emit(media_id)

        self._ipc_thread = threading.Thread(
            target=self._ipc_loop, daemon=True, name="mpv-ipc"
        )
        self._ipc_thread.start()

    def stop(self):
        """停止播放: 通知后台线程收尾, 等它退出, 确保进程结束, 最后落盘"""
        if not self._playing:
            return
        self._playing = False
        self._stop_evt.set()

        # quit 命令和管道句柄都归后台线程收尾, 所以必须先 join 再关,
        # 否则句柄会被两边同时关闭
        t = self._ipc_thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=4)
        self._ipc_thread = None

        proc = self._process
        if proc is not None:
            try:
                proc.wait(timeout=3)
            except Exception:
                # quit 没送达 (例如管道从未连上) 时逐级升级强杀
                for killer in (proc.terminate, proc.kill):
                    try:
                        killer()
                        proc.wait(timeout=2)
                        break
                    except Exception:
                        continue
            self._process = None

        self._save()

    # ---- 后台 IPC 线程 ----

    def _ipc_loop(self):
        """
        后台线程主体: 连管道 -> 按 interval 轮询 -> 只发信号。
        这个线程里不碰 QTimer、QWidget 和数据库 (理由见类 docstring)。
        """
        proc = self._process
        pos = self._position      # play() 里先写好, 线程启动时已可见
        dur = 0

        handle = self._connect_pipe()
        if handle is None:
            if not self._stop_evt.is_set():
                self._ipc_failed.emit(
                    f"连接 MPV 命名管道失败 ({self._pipe_name}, "
                    f"error={self._connect_error})"
                )
            return

        reader = _PipeReader(handle)
        try:
            last = 0.0            # 0 表示进循环就立刻轮询一次, 之后按 interval
            while not self._stop_evt.is_set():
                if proc is not None and proc.poll() is not None:
                    # 用户直接关掉了 mpv 窗口: 先把最后一轮已知进度投出去再报退出,
                    # 两个信号都是 queued, 主线程按序处理, 不会丢掉末尾的进度
                    if pos or dur:
                        self._progress_ready.emit(pos, dur)
                    self._process_exited.emit()
                    return

                if time.monotonic() - last >= self.interval:
                    last = time.monotonic()
                    new_pos = self._query(reader, "time-pos")
                    if new_pos is not None:
                        pos = int(float(new_pos))
                    if dur == 0:                      # 总时长只查一次
                        new_dur = self._query(reader, "duration")
                        if new_dur:
                            dur = int(float(new_dur))
                    if new_pos is not None or dur:
                        self._progress_ready.emit(pos, dur)
                else:
                    # 非轮询轮次也要排空管道: mpv 会持续推事件, 不读走的话
                    # 缓冲区堆积, 下次 await_reply 会读到一堆旧事件
                    reader.pump()
                self._stop_evt.wait(0.05)
        finally:
            if self._stop_evt.is_set():
                reader.send({"command": ["quit"]})    # 优雅关窗
            reader.close()

    def _connect_pipe(self, timeout: float = 10.0) -> Optional[int]:
        """
        等 mpv 把管道建出来并连上, 返回 HANDLE (失败返回 None)。
        必须循环重试: mpv 从启动到管道可用有几百毫秒延迟, 盲 sleep 后只连一次不可靠。
        """
        self._connect_error = 0
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._stop_evt.is_set():
                return None
            h = _kernel32.CreateFileW(
                self._pipe_name,
                GENERIC_READ | GENERIC_WRITE,
                0,               # 不共享
                None,            # 默认安全属性
                OPEN_EXISTING,
                FILE_ATTRIBUTE_NORMAL,
                None,
            )
            if h and h != _INVALID_HANDLE_VALUE:
                return h
            self._connect_error = ctypes.get_last_error()
            self._stop_evt.wait(0.1)
        return None

    def _query(self, reader: _PipeReader, prop: str, timeout: float = 2.0):
        """
        发一条 get_property 并取回 data。
        必须带唯一 request_id: 命令回复和 mpv 主动推的事件行混在一起, 没 id 认不出哪行是回复。
        """
        self._next_request_id += 1
        rid = self._next_request_id
        if not reader.send({"command": ["get_property", prop], "request_id": rid}):
            return None
        return reader.await_reply(rid, timeout)

    # ---- 主线程槽函数 (后台线程只发信号, 状态变更与落库全在这里) ----

    def _on_progress(self, position: int, duration: int):
        if not self._playing:
            return
        if position:
            self._position = position
        if duration:
            self._duration = duration
        self.playback_position.emit(self._position, self._duration)
        self._save()          # 每轮都落库, 防止崩溃丢进度

    def _on_ipc_failed(self, message: str):
        if self._playing:
            self.playback_error.emit(message)

    def _on_process_exited(self):
        """mpv 进程没了 (用户关窗): 落盘 + 通知 UI 清状态条"""
        if not self._playing:
            return
        self._playing = False
        self._stop_evt.set()
        self._process = None
        self._save()
        self.playback_finished.emit(self._media_id)

    def _save(self):
        """保存当前进度到数据库 (只能在主线程调: SQLite 连接不跨线程)"""
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
    构造 vividplayer://openfile?path=<百分号编码路径>。
    路径要整体编码 (safe=""): 盘符冒号、反斜杠、空格、中文都会让对端解析错。
    """
    return "vividplayer://openfile?path=" + urllib.parse.quote(
        file_path, safe=""
    )


class _VividPlayerEngine(QObject):
    """VividPlayer 播放引擎: 纯启动, 不追踪进度, 不支持续播"""

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
        通过 vividplayer://openfile?path=... 协议激活 UWP 版 VividPlayer。
        ShellExecuteW 必须设 restype=HINSTANCE: 默认按 32 位 int 截断返回值,
        失败会被判成成功, 表现为点了没反应也不报错。
        不做 os.startfile() 静默回退: 那样可能用别的播放器打开却报 VividPlayer 生效。
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
    播放控制服务: 统一外观, 按 config['player']['engine'] 选引擎并转发信号。
    "mpv" 是默认; "vividplayer" 纯启动, 无进度追踪。
    """

    playback_started = Signal(int)
    playback_position = Signal(int, int)
    playback_finished = Signal(int)
    playback_error = Signal(str)

    def __init__(self, config: dict, mpv_path: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.config = config
        self.mpv_path = mpv_path          # 重建引擎时要复用, 必须存下来
        self._engine_type: Optional[str] = None    # 必须先存在, _build_engine 会写它
        self._build_engine((config.get("player") or {}).get("engine", "mpv"))

    def _build_engine(self, engine_type: str):
        """构建引擎并转发其信号 (构造与运行时切换共用同一套逻辑)"""
        old = getattr(self, "_engine", None)

        if engine_type == "vividplayer":
            self._engine = _VividPlayerEngine(self.config, self)
            self._engine_type = "vividplayer"
        else:
            if engine_type != "mpv":
                self.playback_error.emit(f"未知的播放引擎: {engine_type}, 回退 MPV")
            self._engine = _MpvEngine(self.config, self.mpv_path, self)
            self._engine_type = "mpv"

        self._engine.playback_started.connect(self.playback_started)
        self._engine.playback_position.connect(self.playback_position)
        self._engine.playback_finished.connect(self.playback_finished)
        self._engine.playback_error.connect(self.playback_error)

        # 旧引擎是 self 的子对象, 不摘父子关系会一直累积,
        # 而且它残留的信号连接仍然有效
        if old is not None and old is not self._engine:
            old.setParent(None)
            old.deleteLater()

    def set_engine(self, engine_type: str) -> bool:
        """运行时切换播放引擎: 先停掉旧的, 再重建 (不用重启 app)"""
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
        """
        当前实际构建出来的引擎类型。
        不能改成读 config: SettingsPage 与这里共用同一个 config dict,
        它先改 config 再调 set_engine, 读 config 会让"没变化就不重建"永远命中。
        """
        return self._engine_type

    def play(self, file_path: str, media_id: int,
             episode_id: int = None, start_pos: int = 0):
        self._engine.play(file_path, media_id, episode_id, start_pos)

    def stop(self):
        self._engine.stop()