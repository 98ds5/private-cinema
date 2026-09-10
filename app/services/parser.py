"""
ffprobe 参数解析服务: 对 parse_status=pending 的文件逐个探测, 把时长/分辨率/帧率/
编码/HDR/音轨/字幕写回 MediaFile。
命令形式: ffprobe -v quiet -print_format json -show_format -show_streams <文件路径>
"""
import json
import subprocess
from datetime import datetime
from typing import Optional

from PySide6.QtCore import QThread, Signal

from app.models.tables import MediaFile
from app.database import get_session

# 单个文件解析超时(秒)
PROBE_TIMEOUT = 60


class ParseWorker(QThread):
    """解析工作线程: 处理所有 parse_status=pending 的文件"""

    parse_started = Signal(int)               # 待解析总数
    parse_progress = Signal(str, str)         # (文件名, 状态: parsing/success/failed)
    parse_finished = Signal(dict)             # {"success":N,"failed":N}
    parse_error = Signal(str)                 # 异常消息

    def __init__(self, ffprobe_path: str, parent=None):
        super().__init__(parent)
        self.ffprobe = ffprobe_path
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        """主流程: 逐个解析 pending 文件并写回结果"""
        self.parse_started.emit(0)

        try:
            with get_session() as session:
                pending = session.query(MediaFile).filter(
                    MediaFile.parse_status == "pending"
                ).all()

                total = len(pending)
                self.parse_started.emit(total)
                success = 0
                failed = 0

                for idx, mf in enumerate(pending):
                    if self._cancelled:
                        break

                    self.parse_progress.emit(mf.file_name, "parsing")

                    try:
                        data = self._probe(mf.file_path)
                        if data:
                            self._apply(mf, data)
                            mf.parse_status = "success"
                            mf.parse_error = None
                            success += 1
                            self.parse_progress.emit(mf.file_name, "success")
                        else:
                            mf.parse_status = "failed"
                            mf.parse_error = "ffprobe 无返回或超时"
                            failed += 1
                            self.parse_progress.emit(mf.file_name, "failed")
                    except Exception as e:
                        mf.parse_status = "failed"
                        mf.parse_error = str(e)
                        failed += 1
                        self.parse_progress.emit(mf.file_name, "failed")

                    mf.parsed_at = datetime.now()

                    # 每 10 条 commit 一次
                    if (idx + 1) % 10 == 0:
                        session.commit()

                session.commit()

        except Exception as e:
            self.parse_error.emit(str(e))

        self.parse_finished.emit({"success": success, "failed": failed})

    def _probe(self, path: str) -> Optional[dict]:
        """调用 ffprobe 并解析返回的 JSON; 失败返回 None"""
        try:
            r = subprocess.run(
                [self.ffprobe, "-v", "quiet",
                 "-print_format", "json",
                 "-show_format", "-show_streams", path],
                capture_output=True, text=True, timeout=PROBE_TIMEOUT,
            )
            if r.returncode != 0:
                return None
            return json.loads(r.stdout)
        except Exception:
            return None

    def _apply(self, mf: MediaFile, data: dict):
        """把 ffprobe 返回的 JSON 写入 MediaFile 字段"""
        fmt = data.get("format", {})
        mf.duration = int(float(fmt.get("duration", 0)))
        mf.file_size = int(fmt.get("size", mf.file_size))
        mf.container_format = fmt.get("format_name", "")

        vs = next((s for s in data.get("streams", [])
                   if s.get("codec_type") == "video"), None)
        if vs:
            mf.width = vs.get("width")
            mf.height = vs.get("height")
            mf.video_codec = vs.get("codec_name")
            mf.hdr_type = self._detect_hdr(vs)
            rfr = vs.get("r_frame_rate", "")
            if "/" in rfr:
                try:
                    num, den = rfr.split("/")
                    mf.frame_rate = round(float(num) / float(den), 3)
                except (ValueError, ZeroDivisionError):
                    pass

        audio_streams = [s for s in data.get("streams", [])
                         if s.get("codec_type") == "audio"]
        if audio_streams:
            mf.audio_codec = audio_streams[0].get("codec_name")
        mf.audio_tracks = json.dumps([
            {"index": s.get("index"), "codec": s.get("codec_name"),
             "lang": s.get("tags", {}).get("language", "und")}
            for s in audio_streams
        ], ensure_ascii=False)

        subs = [s for s in data.get("streams", [])
                if s.get("codec_type") == "subtitle"]
        mf.subtitle_tracks = json.dumps([
            {"index": s.get("index"), "codec": s.get("codec_name"),
             "lang": s.get("tags", {}).get("language", "und")}
            for s in subs
        ], ensure_ascii=False)

    @staticmethod
    def _detect_hdr(vs: dict) -> str:
        """
        按优先级检测 HDR 类型: side_data 里的 DOVI / HDR Dynamic,
        再看 color_transfer (smpte2084=HDR10, arib-std-b67=HLG, 需 bt2020), 都不满足就是 SDR。
        """
        for sd in vs.get("side_data_list", []):
            t = sd.get("side_data_type", "")
            if "DOVI" in t.upper():
                return "Dolby Vision"
            if "HDR Dynamic" in t:
                return "HDR10+"
        ct = vs.get("color_transfer", "")
        cp = vs.get("color_primaries", "")
        if cp == "bt2020":
            if ct == "smpte2084":
                return "HDR10"
            if ct == "arib-std-b67":
                return "HLG"
        return "SDR"