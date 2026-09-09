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

    # PeekNamedPipe: 先问管道里有多少可读字节, 再决定读多少。
    # 没有它就只能阻塞式 ReadFile —— 管道空的时候会把调用线程整个挂死。
    _kernel32.PeekNamedPipe.argtypes = [
        wintypes.HANDLE,            # hNamedPipe
        ctypes.c_void_p,            # lpBuffer
        wintypes.DWORD,             # nBufferSize
        ctypes.POINTER(wintypes.DWORD),  # lpBytesRead
        ctypes.POINTER(wintypes.DWORD),  # lpTotalBytesAvail
        ctypes.POINTER(wintypes.DWORD),  # lpBytesLeftThisMessage
    ]
    _kernel32.PeekNamedPipe.restype = wintypes.BOOL

    # 刻意不再声明 SetNamedPipeHandleState。
    # 探针实测把客户端切到 PIPE_READMODE_MESSAGE 返回 ok=True (mpv 的管道接受),
    # 但本模块自己按字节维护持久缓冲、按 \n 切行, 字节模式才是匹配的语义;
    # 消息模式会让 ReadFile 在消息边界截断, 反而和自己的缓冲打架。

    _HAVE_NAMED_PIPE = True

    # 常量
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
# MPV JSON IPC 的字节流解析 (纯函数 — 可单测, 是 B3 的最小 seam)
# ========================================================================
def _iter_json_lines(data: bytes) -> Tuple[list, bytes]:
    """
    把管道字节流按行切成 JSON 对象。

    返回 (成功解析出的对象列表, 还没凑成完整一行的剩余字节)。
    剩余字节**必须**由调用方存回缓冲 —— mpv 的一行 JSON 可能被拆到两次
    ReadFile 里, 丢了半行就等于丢了那条回复。

    坏行直接跳过: 一行乱码不该毁掉它后面所有正常数据。
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

    判据是 request_id, 不是"第一个带 data 的"。事件行没有 request_id 字段,
    命令回复一定有。反过来, 发命令时不带 request_id 的话回复里恒为 0,
    和事件根本没法区分 —— 这就是为什么每条命令都必须分配唯一 id。
    """
    for o in objects:
        if not isinstance(o, dict) or "event" in o:
            continue
        if o.get("request_id") == request_id:
            return o
    return None


class _PipeReader:
    """
    命名管道上的 newline-delimited JSON 读写 (后台 IPC 线程专用)。

    为什么不能"发一条命令、读一次、json.loads 整块":
      mpv 会**主动推异步事件** (start-file / file-loaded / audio-reconfig ...),
      和命令回复交错在同一次写入里。探针实测: 一次 get_property time-pos
      读回 991 字节 / 11 行, 只有第 1 行是回复, 其余 10 行全是事件。
      整块 json.loads 必然报 "Extra data: line 2 column 1" → 进度恒为 0。

    为什么一律 PeekNamedPipe 而不直接 ReadFile:
      同步管道上没数据时 ReadFile 会把调用线程挂死。挂死的 IPC 线程
      表现为进度条永久停更, 而且没有任何报错。
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
        """非阻塞读走当前所有可读字节 → (解析出的 JSON 对象, 管道是否还活着)"""
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
    MPV 播放引擎 — 通过 JSON IPC 读取进度。

    线程模型 (2026-09-09 重写; 原实现三个 bug 叠加, 进度追踪从未生效过):
      主线程    play()/stop()、全部信号槽、数据库写入
      后台线程  _ipc_loop(): 连管道、轮询 time-pos/duration、只发信号

    两条硬约束决定了必须是这个形状:
      1. QTimer 不能在非 Qt 线程里 start。原实现在普通 threading.Thread 里调
         _timer.start(), Qt 运行时实测直接打出
         "QObject::startTimer: Timers cannot be started from another thread"
         并且定时器永不触发 → _poll 一次都没执行过。
      2. SQLite 连接不能跨线程复用 (pysqlite check_same_thread)。所以落库必须
         留在主线程: 后台线程只发信号, 由主线程槽函数写库。
    """

    playback_started = Signal(int)
    playback_position = Signal(int, int)
    playback_finished = Signal(int)
    playback_error = Signal(str)

    # 后台 IPC 线程 → 主线程的私有通道 (跨线程自动走 queued connection)
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

        # 管道名每次播放必须唯一。
        # 原先是 \\.\pipe\mpv-pc-{media_id}: 上一轮残留的 mpv 进程还占着同名管道时,
        # CreateFileW 会连到**别人的 mpv** 上, 读回来的是别的片子的进度。
        self._pipe_name = rf"\\.\pipe\mpv-pc-{os.getpid()}-{uuid.uuid4().hex[:8]}"

        cmd = [
            self.mpv_path,
            f"--input-ipc-server={self._pipe_name}",
            f"--hwdec={self.hw_decode}",
            "--keep-open=yes",
            "--no-border",
            "--geometry=50%+10%+10%",  # 居中偏上
            # 关掉 mpv 自带的观看位置记忆, 让本 app 的数据库成为唯一事实来源。
            # 实测 (mpv-lazy 的 portable_config\mpv.conf 里有
            # save-position-on-quit=yes + watch-later-options=start,...):
            # 不关的话, 即使我们不传 --start, mpv 也会从它自己缓存的位置接着放
            # —— 真机验证时 start_pos=0 的一轮首个 time-pos 是 625 而不是 0。
            # 那样续播到底听谁的就不确定了, 两边记录还会各自漂移。
            # 命令行优先级高于 mpv.conf, 所以这两条能压住用户的全局配置,
            # 同时不动他的 uosc / 上色 / 着色器等其它设置。
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
            # 原先只捕 FileNotFoundError: mpv 路径指向目录或损坏的 exe 时
            # 抛的是别的 OSError, 会一路穿出 Qt 槽函数, 表现为"点了没反应"。
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
        """停止播放: 通知后台线程收尾 → 等它退出 → 确保进程结束 → 落盘"""
        if not self._playing:
            return
        self._playing = False
        self._stop_evt.set()

        # quit 命令和管道句柄都归后台线程管 (它在 finally 里发 quit 再 close),
        # 所以必须先 join, 否则句柄会被两边同时关闭。
        t = self._ipc_thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=4)
        self._ipc_thread = None

        proc = self._process
        if proc is not None:
            try:
                proc.wait(timeout=3)
            except Exception:
                # quit 没送达 (例如管道从未连上) → 逐级升级强杀
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
        后台线程主体: 连管道 → 按 interval 轮询 → 只发信号。

        绝不在这个线程里碰 QTimer、QWidget 或数据库 (理由见类 docstring)。
        """
        proc = self._process
        pos = self._position      # play() 在 start() 前写好, 线程启动即 happens-before
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
            last = 0.0            # 0 → 进循环立刻轮询一次, 之后按 interval
            while not self._stop_evt.is_set():
                if proc is not None and proc.poll() is not None:
                    # 用户直接关掉了 mpv 窗口。
                    # 先把最后一轮已知进度投出去再报退出: 两个信号都是 queued,
                    # 主线程按序处理, 这样不会丢掉最后 interval 秒的进度。
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
                    # 非轮询轮次也要排空管道: mpv 会持续推事件,
                    # 不读走会让缓冲区堆积, 下次 await_reply 读到一堆陈年事件。
                    reader.pump()
                self._stop_evt.wait(0.05)
        finally:
            if self._stop_evt.is_set():
                reader.send({"command": ["quit"]})    # 优雅关窗
            reader.close()

    def _connect_pipe(self, timeout: float = 10.0) -> Optional[int]:
        """
        等 mpv 把管道建出来并连上, 返回 HANDLE (失败返回 None)。

        必须循环重试: 实测 mpv 从启动到管道可用要 0.69s / 3 次尝试
        (前两次 error=2 ERROR_FILE_NOT_FOUND)。原实现是盲 sleep 满 3 秒后
        **只连一次** —— 冷启动大文件时必然失败, 而且每次都白等 3 秒。
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

        必须带唯一 request_id: mpv 主动推的事件行与命令回复交错 (实测一次
        回复夹带 10 行事件), 不带 id 时所有回复的 request_id 恒为 0,
        根本无法判断哪一行对应本次命令。
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
        """mpv 进程没了 (用户关窗) → 落盘 + 通知 UI 清状态条"""
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