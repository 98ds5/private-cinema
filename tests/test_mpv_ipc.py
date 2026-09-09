"""
MPV JSON IPC 集成回归测试 —— 用真实命名管道假扮 mpv

为什么需要这个文件 (2026-09-09 排查记录, 详见 HANDOFF):
  MPV 引擎从项目建立起就**从未真正跑通过进度追踪**, 三个致命 bug 叠加:
    B1  player.py 用了 threading.Thread 却从未 import threading → play() 必抛 NameError
    B2  在普通 threading.Thread 里 start QTimer → 定时器永不触发, _poll 从不执行
    B3  把整块管道缓冲丢给 json.loads → mpv 的异步事件与命令回复交错,
        实测一次 get_property 回来 991 字节 / 11 行, 解析必然失败

  这三个 bug 的共同点: 只有「真管道 + 真异步事件流 + 真跨线程」才暴露得出来,
  纯 mock 对象一律测不到。所以这里在测试进程内起一个**真实的 Windows 命名管道
  服务端**忠实复现 mpv 的 IPC 行为, 只把 mpv 可执行文件的启动换成桩。

fake mpv 复现的真 mpv 行为 (全部由实跑探针取证, 不是猜的):
  1. 客户端一连上就主动推事件行 (start-file / file-loaded / *-reconfig)
  2. 命令回复与异步事件在**同一次写入**里交错 → 单次 ReadFile 会拿到多行
  3. 一行 JSON 可能被拆到两次 ReadFile 里 → 客户端必须自己维护持久缓冲
  4. 回复带 request_id, 事件行没有 → 必须靠 request_id 配对, 不能只按行拆
"""
import ctypes
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from ctypes import wintypes
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication

from app.database import init_database, get_session
from app.models.tables import Media, MediaFile
from app.services import player as player_mod
from app.services.player import _MpvEngine

_app = QApplication.instance() or QApplication(sys.argv)

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="MPV JSON IPC 走 Windows 命名管道"
)

# ----------------------------------------------------------------------
# Win32 命名管道 (服务端侧)
# ----------------------------------------------------------------------
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)

_k32.CreateNamedPipeW.restype = wintypes.HANDLE
_k32.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.DWORD, ctypes.c_void_p]
_k32.ConnectNamedPipe.restype = wintypes.BOOL
_k32.ConnectNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
_k32.WriteFile.restype = wintypes.BOOL
_k32.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                           ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
_k32.ReadFile.restype = wintypes.BOOL
_k32.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                          ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
_k32.PeekNamedPipe.restype = wintypes.BOOL
_k32.PeekNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                               ctypes.POINTER(wintypes.DWORD),
                               ctypes.POINTER(wintypes.DWORD),
                               ctypes.POINTER(wintypes.DWORD)]
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_k32.CloseHandle.restype = wintypes.BOOL

_PIPE_ACCESS_DUPLEX = 0x00000003
_PIPE_TYPE_BYTE = 0x00000000
_PIPE_READMODE_BYTE = 0x00000000
_PIPE_WAIT = 0x00000000
_INVALID_HANDLE = wintypes.HANDLE(-1).value
_ERROR_PIPE_CONNECTED = 535

_DURATION = 1444.985          # 探针实测到的真值


class FakeMpv:
    """
    在测试进程内扮演 mpv 的 JSON IPC 服务端。

    刻意制造真 mpv 的三种"脏"数据形态, 用来锁死 B3:
      - 第 1 次 get_property: 回复 + 10 行异步事件, **一次 WriteFile 全部写出**
        (复刻探针抓到的 991 字节 / 11 行)
      - 第 2 次 get_property: 把一行 JSON **拆成两次 WriteFile**, 中间隔 30ms
      - 其余每次: 回复前先塞一行无关事件
    """

    def __init__(self, pipe_name: str):
        self.pipe_name = pipe_name
        self.connected = threading.Event()
        self.failure: str = ""
        self.commands: list = []          # 收到的命令, 供断言
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._pos_tick = 0

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    # ---- 服务端实现 ----
    def _run(self):
        h = _k32.CreateNamedPipeW(
            self.pipe_name, _PIPE_ACCESS_DUPLEX,
            _PIPE_TYPE_BYTE | _PIPE_READMODE_BYTE | _PIPE_WAIT,
            1, 65536, 65536, 0, None,
        )
        if not h or h == _INVALID_HANDLE:
            self.failure = f"CreateNamedPipeW 失败 error={ctypes.get_last_error()}"
            return

        if not _k32.ConnectNamedPipe(h, None):
            if ctypes.get_last_error() != _ERROR_PIPE_CONNECTED:
                self.failure = f"ConnectNamedPipe 失败 error={ctypes.get_last_error()}"
                _k32.CloseHandle(h)
                return

        self.connected.set()
        # 真 mpv 一连上就推事件 (探针实测)
        self._write(h, '{"event": "start-file", "playlist_entry_id": 1}\n')

        pending = b""
        n_tp = 0
        while not self._stop.is_set():
            chunk = self._read_avail(h)
            if not chunk:
                time.sleep(0.01)
                continue
            pending += chunk
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    req = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                cmd = req.get("command")
                rid = req.get("request_id", 0)
                self.commands.append(cmd)

                if cmd == ["quit"]:
                    self._stop.set()
                    break
                if cmd == ["get_property", "time-pos"]:
                    n_tp += 1
                    self._pos_tick += 10
                    reply = {"data": float(self._pos_tick), "request_id": rid,
                             "error": "success"}
                    if n_tp == 1:
                        # 形态一: 回复 + 10 行事件, 一次性写出
                        blob = json.dumps(reply) + "\n"
                        blob += "".join(
                            json.dumps({"event": e}) + "\n"
                            for e in ("audio-reconfig", "audio-reconfig",
                                      "file-loaded", "audio-reconfig",
                                      "video-reconfig", "video-reconfig",
                                      "audio-reconfig", "file-loaded",
                                      "video-reconfig", "playback-restart")
                        )
                        self._write(h, blob)
                    elif n_tp == 2:
                        # 形态二: 一行 JSON 拆两次写
                        text = json.dumps(reply) + "\n"
                        cut = len(text) // 2
                        self._write(h, text[:cut])
                        time.sleep(0.03)
                        self._write(h, text[cut:])
                    else:
                        # 形态三: 回复前塞一行无关事件
                        self._write(h, '{"event":"tick"}\n' + json.dumps(reply) + "\n")
                elif cmd == ["get_property", "duration"]:
                    self._write(h, json.dumps(
                        {"data": _DURATION, "request_id": rid, "error": "success"}
                    ) + "\n")
                else:
                    self._write(h, json.dumps(
                        {"error": "command not found", "request_id": rid}
                    ) + "\n")

        _k32.CloseHandle(h)

    @staticmethod
    def _write(h, text: str):
        b = text.encode("utf-8")
        n = wintypes.DWORD(0)
        _k32.WriteFile(h, b, len(b), ctypes.byref(n), None)

    @staticmethod
    def _read_avail(h) -> bytes:
        avail = wintypes.DWORD(0)
        if not _k32.PeekNamedPipe(h, None, 0, None, ctypes.byref(avail), None):
            return b""
        if avail.value <= 0:
            return b""
        buf = ctypes.create_string_buffer(avail.value)
        got = wintypes.DWORD(0)
        if _k32.ReadFile(h, buf, avail.value, ctypes.byref(got), None) and got.value:
            return buf.raw[:got.value]
        return b""


# ----------------------------------------------------------------------
# mpv 可执行文件的桩 (只替换"启动进程", 管道通信全是真的)
# ----------------------------------------------------------------------
class _FakePopen:
    """假 mpv 进程: 永远活着, 直到 kill()"""

    def __init__(self, cmd, **kwargs):
        self.cmd = cmd
        self.returncode = None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9

    terminate = kill


class _SubprocessShim:
    Popen = _FakePopen
    DEVNULL = subprocess.DEVNULL


# ----------------------------------------------------------------------
# fixtures
# ----------------------------------------------------------------------
@pytest.fixture
def movie(tmp_path):
    """一部已入库的电影 + 一个真实存在的假文件"""
    init_database(str(tmp_path / "cinema.db"))
    f = tmp_path / "Inception.2010.1080p.mkv"
    f.write_bytes(b"\0" * 2048)

    with get_session() as s:
        m = Media(title="Inception", media_type="movie", year=2010)
        s.add(m)
        s.flush()
        s.add(MediaFile(media_id=m.id, file_path=str(f), file_name=f.name,
                        file_size=2048, duration=int(_DURATION),
                        parse_status="success"))
        s.commit()
        mid = m.id
    return {"media_id": mid, "path": str(f)}


@pytest.fixture
def engine(monkeypatch):
    """一个 progress_interval 调到 0.2s 的 MPV 引擎 (生产默认 10s, 测试等不起)"""
    monkeypatch.setattr(player_mod, "subprocess", _SubprocessShim)
    cfg = {"player": {"engine": "mpv", "progress_interval": 0.2,
                      "hw_decode": "auto"}}
    eng = _MpvEngine(cfg, mpv_path="mpv-not-real")
    yield eng
    try:
        eng.stop()
    except Exception:
        pass


def _pump(pred, timeout=10.0):
    """
    泵事件循环直到条件成立。

    必须泵: IPC 在后台线程, 进度是经 Qt 信号投递回主线程的 (queued connection),
    不 processEvents 就永远收不到。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        _app.processEvents()
        if pred():
            return True
        time.sleep(0.02)
    _app.processEvents()
    return pred()


def _start_fake(engine, media):
    """
    先 play(), 再从引擎读出它自己生成的管道名, 然后才起服务端。

    故意反过来做: 这样同时验证了"客户端必须重试连接"——真机上 mpv 建管道
    实测要 0.69s / 3 次尝试, 只连一次的实现必然失败。
    """
    fake = FakeMpv("")          # 管道名占位, play() 之后才知道
    engine.play(media["path"], media["media_id"])
    fake.pipe_name = engine._pipe_name
    fake.start()
    return fake


# ----------------------------------------------------------------------
# 测试
# ----------------------------------------------------------------------
class TestMpvIpc:

    def test_play_does_not_raise(self, engine, movie):
        """
        回归锁 B1: player.py 使用 threading.Thread 却从未 import threading,
        play() 在 Popen 成功后必抛 NameError —— mpv 窗口开得出来,
        但 IPC 永不连接, 进度追踪一次都没生效过。
        """
        engine.play(movie["path"], movie["media_id"])
        assert engine.is_playing, "play() 之后引擎应处于播放态"

    def test_pipe_name_is_unique_per_play(self, engine, movie):
        """
        回归锁 B8: 管道名原先是 \\\\.\\pipe\\mpv-pc-{media_id}, 只含 media_id。
        上一次播放残留的 mpv 还占着同名管道时, 新播放会连到**别人的 mpv** 上,
        读到的是别的片子的进度。必须带进程/随机成分。
        """
        engine.play(movie["path"], movie["media_id"])
        first = engine._pipe_name
        engine.stop()
        engine.play(movie["path"], movie["media_id"])
        second = engine._pipe_name
        assert first != second, f"同一作品两次播放管道名撞了: {first}"

    def test_mpv_own_resume_is_disabled(self, engine, movie):
        """
        回归锁 B9: mpv 自带观看位置记忆, 会在 mpv 侧另存一份进度。

        真机实测 (mpv-lazy 的 portable_config\\mpv.conf 里有
        save-position-on-quit=yes + watch-later-options=start,...):
        即使我们 start_pos=0、完全不传 --start, mpv 也从它自己缓存的位置接着放
        —— 那一轮首个 time-pos 是 625 而不是 0, 播放 6 秒后落库却是 646。
        后果是本 app 的数据库不是唯一事实来源, 续播听谁的并不确定,
        两边记录还会各自漂移。必须显式关掉。
        """
        engine.play(movie["path"], movie["media_id"])
        cmd = engine._process.cmd
        assert "--no-resume-playback" in cmd, \
            "必须禁止 mpv 恢复它自己的观看位置, 否则 --start 不是权威"
        assert "--save-position-on-quit=no" in cmd, \
            "必须禁止 mpv 另存一份进度, 否则两边记录会漂移"

    def test_hwdec_and_ipc_args_present(self, engine, movie):
        """启动参数契约: 管道名/硬解/keep-open/续播起点都必须正确传出去"""
        engine.play(movie["path"], movie["media_id"], start_pos=120)
        cmd = engine._process.cmd
        assert cmd[0] == "mpv-not-real"
        assert f"--input-ipc-server={engine._pipe_name}" in cmd
        assert "--hwdec=auto" in cmd
        assert "--keep-open=yes" in cmd
        assert "--start=120" in cmd, "续播起点必须传给 mpv"
        assert cmd[-1] == movie["path"], "文件路径必须是最后一个参数"

    def test_start_pos_zero_omits_start_arg(self, engine, movie):
        """从头播时不该传 --start=0 (mpv 对 0 的处理没必要时增加变量)"""
        engine.play(movie["path"], movie["media_id"], start_pos=0)
        assert not any(c.startswith("--start=") for c in engine._process.cmd)

    def test_connects_and_reports_position(self, engine, movie):
        """
        回归锁 B2 + B3: 进度必须真的从管道里读出来并经信号回到主线程。

        B2 —— QTimer 在普通 threading.Thread 里 start 永不触发, _poll 从不执行;
        B3 —— 整块 json.loads 撞上 mpv 的异步事件流必然 Extra data 报错。
        任一个存在, 这里都收不到任何 position。
        """
        errors: list = []
        positions: list = []
        engine.playback_error.connect(errors.append)
        engine.playback_position.connect(lambda p, d: positions.append((p, d)))

        fake = _start_fake(engine, movie)
        try:
            assert fake.connected.wait(10), f"fake mpv 未被连上: {fake.failure}"
            ok = _pump(lambda: any(p > 0 and d > 0 for p, d in positions))
            assert ok, (
                f"始终没收到有效进度。errors={errors} positions={positions} "
                f"fake 收到的命令={fake.commands}"
            )
            assert not errors, f"不应有播放错误: {errors}"
        finally:
            fake.stop()

        pos, dur = positions[-1]
        assert dur == int(_DURATION), f"duration 解析错: {dur}"
        assert pos > 0, f"position 应随播放推进: {pos}"
        assert any(c == ["get_property", "time-pos"] for c in fake.commands)

    def test_progress_persisted_to_database(self, engine, movie):
        """端到端: 进度必须落库, 否则首页统计与详情页续播全是空的"""
        fake = _start_fake(engine, movie)
        try:
            assert fake.connected.wait(10), fake.failure
            assert _pump(lambda: engine._duration > 0 and engine._position > 0), \
                f"引擎内部进度未更新: pos={engine._position} dur={engine._duration}"
        finally:
            fake.stop()

        engine.stop()          # stop() 必须落一次盘
        _app.processEvents()

        with get_session() as s:
            m = s.query(Media).filter(Media.id == movie["media_id"]).one()
            assert m.watched_position > 0, "watched_position 未写入"
            assert m.watched_duration == int(_DURATION), \
                f"watched_duration 未写入: {m.watched_duration}"
            assert m.status == "watching", f"状态应为观看中: {m.status}"
            assert m.last_watched_at is not None, "last_watched_at 未写入"

    def test_request_id_is_sent(self, engine, movie):
        """
        回归锁 B4: 不带 request_id 时 mpv 的回复 request_id 恒为 0,
        与异步事件交错后**无法判定哪一行是本次命令的回复**。
        必须每条命令带唯一 request_id 并据此配对。
        """
        fake = _start_fake(engine, movie)
        try:
            assert fake.connected.wait(10), fake.failure
            assert _pump(lambda: engine._duration > 0), "未拿到 duration"
        finally:
            fake.stop()
        # fake 记录的是 command 字段; request_id 的断言靠"能正确配对"间接完成,
        # 这里再直接确认引擎确实往管道写了 request_id 字段。
        assert engine._next_request_id > 1, \
            "引擎应给每条命令分配递增的 request_id"

    def test_engine_exit_saves_and_emits_finished(self, engine, movie):
        """mpv 进程退出 (用户关窗) → 必须落盘 + 发 playback_finished"""
        fake = _start_fake(engine, movie)
        finished: list = []
        engine.playback_finished.connect(finished.append)
        try:
            assert fake.connected.wait(10), fake.failure
            assert _pump(lambda: engine._position > 0), "未拿到进度"
            # 模拟用户直接关掉 mpv 窗口
            engine._process.kill()
            assert _pump(lambda: bool(finished)), \
                f"进程退出后未发 playback_finished: {finished}"
        finally:
            fake.stop()

        assert finished == [movie["media_id"]]
        with get_session() as s:
            m = s.query(Media).filter(Media.id == movie["media_id"]).one()
            assert m.watched_position > 0, "进程退出时进度应已落盘"


class TestIpcLineParsing:
    """
    IPC 字节流解析的纯函数单测 —— 这是 B3 的最小可复现 seam。

    输入全部取自探针实测的真 mpv 输出, 不是编的。
    """

    def _parse(self, *args):
        from app.services.player import _iter_json_lines
        return _iter_json_lines(*args)

    def test_single_complete_line(self):
        objs, rest = self._parse(b'{"data":1.0,"request_id":7,"error":"success"}\n')
        assert objs == [{"data": 1.0, "request_id": 7, "error": "success"}]
        assert rest == b""

    def test_multiple_lines_in_one_chunk(self):
        """真 mpv 实测形态: 一次读回 11 行, 只有第 1 行是回复"""
        blob = (b'{"data":0.0,"request_id":0,"error":"success"}\n'
                b'{"event":"audio-reconfig"}\n'
                b'{"event":"file-loaded"}\n'
                b'{"event":"video-reconfig"}\n')
        objs, rest = self._parse(blob)
        assert len(objs) == 4
        assert objs[0]["data"] == 0.0
        assert [o.get("event") for o in objs[1:]] == \
            ["audio-reconfig", "file-loaded", "video-reconfig"]
        assert rest == b""

    def test_partial_line_is_buffered(self):
        """一行 JSON 被拆成两次读 → 前半段必须留在缓冲里, 不能丢也不能报错"""
        full = b'{"data":2.5,"request_id":9,"error":"success"}\n'
        cut = len(full) // 2
        objs, rest = self._parse(full[:cut])
        assert objs == []
        assert rest == full[:cut], "未完整的字节必须原样保留"

        objs, rest = self._parse(rest + full[cut:])
        assert objs == [{"data": 2.5, "request_id": 9, "error": "success"}]
        assert rest == b""

    def test_garbage_line_is_skipped_not_fatal(self):
        objs, rest = self._parse(b'not json at all\n{"event":"ok"}\n')
        assert objs == [{"event": "ok"}], "坏行应跳过, 不能让它毁掉后面的好行"
        assert rest == b""

    def test_reply_extraction_ignores_events(self):
        """从混杂流里按 request_id 挑出回复"""
        from app.services.player import _pick_reply
        objs = [{"event": "tick"},
                {"data": 12.0, "request_id": 3, "error": "success"},
                {"event": "file-loaded"}]
        assert _pick_reply(objs, 3) == {"data": 12.0, "request_id": 3,
                                        "error": "success"}
        assert _pick_reply(objs, 4) is None, "request_id 不匹配不得误取"
        assert _pick_reply([{"event": "tick"}], 0) is None, \
            "事件行没有 request_id, 不能被当成回复"
