"""ffprobe 解析 (ParseWorker) 的行为锁。

真实事故: 打包版里 12/24 个文件被误判成 "ffprobe 无返回或超时"。
根因是 `subprocess.run(..., text=True)` 没给 encoding, 于是按系统 ANSI 码页
(cp936) 解码 ffprobe 的 JSON; 输出里带 mkv 的 UTF-8 元数据(片名/标签)时解码线程
抛 UnicodeDecodeError, `r.stdout` 直接变成 None, json.loads(None) 抛 TypeError,
被 _probe 的 except 一并吞掉 —— 失败原因看着像"ffprobe 没返回", 其实是我们自己
解码炸了。开发机带着 PYTHONUTF8=1, 所以只有打包后才暴露。
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.parser import ParseWorker  # noqa: E402


def _ffprobe_json(duration="5706.666", title="帕丁顿熊"):
    """一份贴近真输出的 ffprobe JSON (format + streams)"""
    return json.dumps({
        "format": {"duration": duration, "size": "60561000000",
                   "format_name": "matroska,webm",
                   "tags": {"title": title}},
        "streams": [
            {"index": 0, "codec_type": "video", "codec_name": "hevc",
             "width": 3840, "height": 2160, "r_frame_rate": "24000/1001",
             "color_transfer": "smpte2084", "color_primaries": "bt2020"},
            {"index": 1, "codec_type": "audio", "codec_name": "truehd",
             "tags": {"language": "eng"}},
        ],
    }, ensure_ascii=False)


class _ChildProcess:
    """按真实机制作妖的替身: 解码方式不对就"炸在读取线程里"。

    CPython 的 subprocess 在 text 模式下用后台线程读管道; 解码失败抛在**那个线程**里,
    主线程只看到 `stdout=None`。所以替身不是抛异常, 而是返回 stdout=None。
    """

    def __init__(self, payload: str):
        self.payload = payload
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(kwargs)
        enc = (kwargs.get("encoding") or "").lower().replace("_", "-")
        if enc not in ("utf-8", "utf8"):
            # 复刻 cp936 解码不了的后果: 读取线程挂了 -> stdout 是 None
            return subprocess.CompletedProcess(cmd, 0, None, None)
        return subprocess.CompletedProcess(cmd, 0, self.payload, "")


@pytest.fixture
def worker():
    return ParseWorker("ffprobe")


class TestProbeDecoding:
    def test_parses_utf8_metadata_without_pythonutf8(self, worker, monkeypatch):
        """没有 PYTHONUTF8=1(打包版的默认状态)也必须能解析"""
        child = _ChildProcess(_ffprobe_json())
        monkeypatch.setattr(subprocess, "run", child)
        monkeypatch.delenv("PYTHONUTF8", raising=False)
        assert worker._probe(r"D:\Movie资源\帕丁顿1\Paddington.mkv") is not None

    def test_asks_for_utf8_explicitly(self, worker, monkeypatch):
        """锁住修复本身: 不许再靠系统码页"""
        child = _ChildProcess(_ffprobe_json())
        monkeypatch.setattr(subprocess, "run", child)
        worker._probe("x.mkv")
        kwargs = child.calls[0]
        assert (kwargs.get("encoding") or "").lower().replace("_", "-") == "utf-8"
        assert kwargs.get("errors") == "replace", \
            "坏字节要替换掉, 不能让整条解码线程挂掉"

    def test_nonzero_exit_is_still_a_failure(self, worker, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kw:
                            subprocess.CompletedProcess(cmd, 1, "", "boom"))
        assert worker._probe("x.mkv") is None

    def test_garbage_output_is_a_failure(self, worker, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kw:
                            subprocess.CompletedProcess(cmd, 0, "{not json", ""))
        assert worker._probe("x.mkv") is None


class TestApply:
    """探测结果落到 MediaFile 上 (时长/分辨率/HDR/音轨)"""

    def _file(self):
        from app.models.tables import MediaFile
        return MediaFile(media_id=1, file_path="x.mkv", file_name="x.mkv")

    def test_writes_duration_and_video_fields(self, worker):
        mf = self._file()
        worker._apply(mf, json.loads(_ffprobe_json()))
        assert mf.duration == 5706
        assert (mf.width, mf.height) == (3840, 2160)
        assert mf.video_codec == "hevc"
        assert mf.hdr_type == "HDR10"
        assert mf.frame_rate == pytest.approx(23.976, abs=0.01)

    def test_duration_is_an_int_even_when_ffprobe_gives_float(self, worker):
        mf = self._file()
        worker._apply(mf, json.loads(_ffprobe_json(duration="1440.022")))
        assert mf.duration == 1440 and isinstance(mf.duration, int)

    def test_audio_tracks_keep_language(self, worker):
        mf = self._file()
        worker._apply(mf, json.loads(_ffprobe_json()))
        tracks = json.loads(mf.audio_tracks)
        assert tracks[0]["codec"] == "truehd" and tracks[0]["lang"] == "eng"
