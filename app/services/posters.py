"""
海报提取 —— A 方案: 从本地视频文件里抽内嵌封面, **不联网刮削**。

优先级 (ensure_poster):
  1. 已经抽好的 `data/posters/{media_id}.jpg` —— 直接复用, 不重复跑 ffmpeg
  2. 视频内嵌封面: mkv/mp4 里 disposition.attached_pic=1 的那条视频流
     (ffprobe 找流号 → ffmpeg 单帧抽出)
  3. 视频同目录的手动图片: poster/cover/folder/front.<jpg|png|webp>, 或 <视频同名>.jpg
  4. 都没有 → 返回 None, UI 继续用现在的首字母占位

刻意不联网: 需求里没有刮削, 演示环境也不一定有网。
文件名统一用 media_id 而不是片名 —— 中文/空格/超长片名当文件名太容易出事。
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Optional, Tuple

from PySide6.QtCore import QThread, Signal

from app.database import get_session
from app.models.tables import Media, MediaFile

# 抽一帧封面而已, 不该让它跑满 60 秒
PROBE_TIMEOUT = 30
EXTRACT_TIMEOUT = 60

# 手动海报的候选文件名 (小写比较), 按优先级排
_MANUAL_NAMES = ("poster", "cover", "folder", "front", "art")
_MANUAL_EXTS = (".jpg", ".jpeg", ".png", ".webp")

# Windows 上跑命令行工具要压掉那个黑框, 否则每抽一张封面闪一次控制台
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def posters_dir() -> Path:
    """`<项目根>/data/posters`。用 __file__ 推项目根, 不依赖当前工作目录。"""
    return Path(__file__).resolve().parents[2] / "data" / "posters"


def poster_file(media_id: int) -> Path:
    return posters_dir() / f"{int(media_id)}.jpg"


def _run(cmd: list, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        creationflags=_NO_WINDOW,
        encoding="utf-8",
        errors="replace",
    )


def find_attached_pic(ffprobe: Optional[str], video_path: str) -> Optional[int]:
    """
    返回内嵌封面所在的视频流号 (整体流号, 可直接喂给 ffmpeg -map 0:{n}), 没有则 None。

    attached_pic 是 ffmpeg 给"封面图"这条流打的 disposition 标记 ——
    mkv 的 attachment 和 mp4 的 cover art 都会被识别成它。
    """
    if not ffprobe or not video_path or not os.path.isfile(video_path):
        return None
    try:
        r = _run([
            ffprobe, "-v", "error",
            "-print_format", "json",
            "-show_streams",
            "-select_streams", "v",
            video_path,
        ], PROBE_TIMEOUT)
    except (subprocess.SubprocessError, OSError):
        return None
    if r.returncode != 0:
        return None
    try:
        streams = json.loads(r.stdout or "{}").get("streams", [])
    except (json.JSONDecodeError, AttributeError):
        return None

    for s in streams:
        if not isinstance(s, dict):
            continue
        disp = s.get("disposition") or {}
        if str(disp.get("attached_pic", 0)) == "1":
            idx = s.get("index")
            return int(idx) if idx is not None else None
    return None


def extract_stream(ffmpeg: Optional[str], video_path: str,
                   stream_index: int, out_path: Path) -> bool:
    """把指定流的一帧抽成 jpg。成功返回 True。"""
    if not ffmpeg or not video_path or not os.path.isfile(video_path):
        return False
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        r = _run([
            ffmpeg, "-v", "error", "-y",
            "-i", video_path,
            "-map", f"0:{int(stream_index)}",
            "-frames:v", "1",
            str(out_path),
        ], EXTRACT_TIMEOUT)
    except (subprocess.SubprocessError, OSError):
        return False
    # ffmpeg 有时会返回 0 却没写出文件, 所以以文件为准
    return r.returncode == 0 and out_path.is_file() and out_path.stat().st_size > 0


def find_manual_poster(video_path: str) -> Optional[Path]:
    """在视频同目录找手动放的海报图: poster/cover/folder/front/art 或 <视频同名>。"""
    if not video_path:
        return None
    p = Path(video_path)
    folder = p.parent
    if not folder.is_dir():
        return None

    stem = p.stem.lower()
    wanted = [f"{n}{e}" for n in _MANUAL_NAMES for e in _MANUAL_EXTS]
    wanted += [f"{stem}{e}" for e in _MANUAL_EXTS]

    try:
        entries = {f.name.lower(): f for f in folder.iterdir() if f.is_file()}
    except OSError:
        return None
    for name in wanted:
        hit = entries.get(name)
        if hit is not None:
            return hit
    return None


def ensure_poster(media_id: int, file_paths: Iterable[str],
                  ffprobe: Optional[str], ffmpeg: Optional[str],
                  force: bool = False) -> Tuple[Optional[str], Optional[str]]:
    """
    保证这部作品有一张海报, 返回 (路径, 来源)。来源 ∈ cached/embedded/manual, 拿不到则 (None, None)。

    一部作品可能有多个文件 (动漫一季十几集), 挨个试, 第一个成功的就用它。
    """
    out = poster_file(media_id)
    if not force and out.is_file() and out.stat().st_size > 0:
        return str(out), "cached"

    paths = [p for p in (file_paths or []) if p]
    for p in paths:
        idx = find_attached_pic(ffprobe, p)
        if idx is not None and extract_stream(ffmpeg, p, idx, out):
            return str(out), "embedded"

    for p in paths:
        manual = find_manual_poster(p)
        if manual is not None:
            try:
                out.parent.mkdir(parents=True, exist_ok=True)
                # 用复制而不是软链接: 用户可能之后把图片挪走
                out.write_bytes(manual.read_bytes())
                if out.stat().st_size > 0:
                    return str(out), "manual"
            except OSError:
                continue
    return None, None


class PosterWorker(QThread):
    """给整个库补海报。放在线程里跑: 十几个 ffmpeg 子进程会把 UI 卡住。"""

    poster_started = Signal(int)          # 待处理作品数
    poster_progress = Signal(str, str)    # (标题, 状态: cached/embedded/manual/none/failed)
    poster_finished = Signal(dict)        # 各来源计数
    poster_error = Signal(str)

    def __init__(self, ffprobe_path: Optional[str], ffmpeg_path: Optional[str],
                 force: bool = False, parent=None):
        super().__init__(parent)
        self.ffprobe = ffprobe_path
        self.ffmpeg = ffmpeg_path
        self.force = force
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        counts = {"cached": 0, "embedded": 0, "manual": 0, "none": 0, "failed": 0}
        try:
            with get_session() as s:
                media = s.query(Media).order_by(Media.id).all()
                self.poster_started.emit(len(media))

                for m in media:
                    if self._cancelled:
                        break
                    title = m.title or f"#{m.id}"
                    files = s.query(MediaFile.file_path).filter(
                        MediaFile.media_id == m.id).all()
                    paths = [f[0] for f in files if f[0]]

                    try:
                        path, source = ensure_poster(
                            m.id, paths, self.ffprobe, self.ffmpeg, force=self.force)
                    except Exception as e:      # 单个文件坏了不该中断整批
                        counts["failed"] += 1
                        self.poster_progress.emit(title, "failed")
                        self.poster_error.emit(f"{title}: {type(e).__name__}: {e}")
                        continue

                    if path:
                        m.poster_path = path
                        counts[source] = counts.get(source, 0) + 1
                        self.poster_progress.emit(title, source)
                    else:
                        counts["none"] += 1
                        self.poster_progress.emit(title, "none")

                s.commit()
        except Exception as e:
            self.poster_error.emit(f"{type(e).__name__}: {e}")

        self.poster_finished.emit(counts)
