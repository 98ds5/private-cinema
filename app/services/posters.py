"""
海报提取 —— 纯离线, 不联网刮削。

`ensure_poster` 的优先级 (用户意图越强的越靠前):
  1. 已经生成好的 `data/posters/{media_id}.jpg` —— 直接复用, 不重复跑 ffmpeg
  2. 视频同目录的**用户手动图片**: poster/cover/folder/front/art.<jpg|jpeg|png|webp>
     或 <视频同名>.jpg —— 用户自己丢的图是最强的意图信号, 压过内嵌封面
  3. 视频**内嵌封面**: disposition.attached_pic=1 的那条流
  4. **自动截帧**: 时长的 10% → 30% → 50%, 每帧做黑屏检测, 不合格就换下一个
  5. 都没有 → (None, None), UI 按 media_type 显示占位

== 刻意偏离 GPT 那份规范的四处, 以及理由 ==
- **不用视频文件 MD5 当文件名。** 库里有 74.6 GB 的 4K 片源, 分块读也照样要把
  74GB 全过一遍磁盘, 为了抽一张封面先读几十分钟。改用 `media_id`(已有稳定主键);
  要判断片源是否被换过, 用库里现成的 `file_size` / `duration` 就够, 不必算哈希。
- **不在项目里内置 ffmpeg/ 目录。** 上百 MB 二进制塞进课程仓库不合适;
  `detect_ffmpeg()` 会先找 PATH, 再去 ffprobe 同目录找(本机 WinGet 的 build 两个 exe 在一起)。
- **不强制 resize 成 320x448 竖版。** 真库里 `杀死比尔` 的内嵌图是 640x360 的
  **横版剧照**, 硬裁成 2:3 会切掉三分之二画面。改成"长边不超过 448、保持比例、
  不放大", 填充裁切交给 UI (`MediaCard._apply_poster`) 去做。
- **手动选帧不能用 QMediaPlayer/QVideoWidget**: 本项目只装了 PySide6-**Essentials**,
  没有 Addons → 没有 QtMultimedia。

文件名统一用 media_id 而不是片名 —— 中文/空格/超长片名当文件名太容易出事。
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Optional, Sequence, Tuple

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QImage

from app.database import get_session
from app.models.tables import Media, MediaFile

# 抽一帧封面而已, 不该让它跑满 60 秒
PROBE_TIMEOUT = 30
EXTRACT_TIMEOUT = 60

# 手动海报的候选文件名 (小写比较), 按优先级排
_MANUAL_NAMES = ("poster", "cover", "folder", "front", "art")
_MANUAL_EXTS = ("jpg", "jpeg", "png", "webp")
_MANUAL_CANDIDATES = tuple(f"{n}.{e}" for n in _MANUAL_NAMES for e in _MANUAL_EXTS)

# 存图统一压到长边不超过 _BOX, 保持比例、不放大, JPEG 质量 _QV
_BOX = 448
_QV = "3"
_VF_NORMALIZE = (
    "scale=w='min({b},iw)':h='min({b},ih)'"
    ":force_original_aspect_ratio=decrease:force_divisible_by=2"
).format(b=_BOX)

# 自动截帧: 依次试这几个时间点; 片头片尾容易是黑场/logo, 所以从 10% 起
FRAME_RATIOS = (0.10, 0.30, 0.50)
DARK_THRESHOLD = 25          # 平均亮度低于这个值判定为黑屏
_SAFETY_MARGIN = 1.0         # 秒: 别贴着片尾截, 否则 ffmpeg 截不到帧

# Windows 上跑命令行工具要压掉那个黑框, 否则每抽一张封面闪一次控制台
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def posters_dir() -> Path:
    """`<项目根>/data/posters`。用 __file__ 推项目根, 不依赖当前工作目录。"""
    return Path(__file__).resolve().parents[2] / "data" / "posters"


def poster_file(media_id: int) -> Path:
    return posters_dir() / f"{int(media_id)}.jpg"


def _tmp_out(out: Path) -> Path:
    """
    ffmpeg 的落盘目标用临时名, 成功后再 `os.replace` 原子改名。

    直接让 ffmpeg 写最终文件的话, 它中途失败会留下一个**半截的 jpg**,
    而下一次跑看见"文件已存在"就当缓存用了 → 永久显示成半张烂图。
    """
    return out.with_name(f"{out.stem}.tmp{out.suffix}")


def _finalize(tmp: Path, out: Path) -> bool:
    """临时文件校验通过就原子替换到最终位置。以文件为准, 不看 ffmpeg 的返回码。"""
    try:
        if not tmp.is_file() or tmp.stat().st_size == 0:
            return False
        out.parent.mkdir(parents=True, exist_ok=True)
        os.replace(str(tmp), str(out))
        return out.is_file() and out.stat().st_size > 0
    except OSError:
        return False
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


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


# ----------------------------------------------------------------------
# ffprobe
# ----------------------------------------------------------------------
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
            "-show_streams", "-select_streams", "v",
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
        # 有些 ffmpeg 版本会把它序列化成字符串 "1" 而不是数字 1
        if str(disp.get("attached_pic", 0)) == "1":
            idx = s.get("index")
            return int(idx) if idx is not None else None
    return None


def get_duration(ffprobe: Optional[str], video_path: str) -> Optional[float]:
    """视频总时长(秒)。拿不到返回 None —— 这时不能瞎猜时间点去截帧。"""
    if not ffprobe or not video_path or not os.path.isfile(video_path):
        return None
    try:
        r = _run([
            ffprobe, "-v", "error",
            "-print_format", "json", "-show_format",
            video_path,
        ], PROBE_TIMEOUT)
    except (subprocess.SubprocessError, OSError):
        return None
    if r.returncode != 0:
        return None
    try:
        raw = json.loads(r.stdout or "{}").get("format", {}).get("duration")
        val = float(raw)
    except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
        return None
    return val if val > 0 else None


# ----------------------------------------------------------------------
# ffmpeg
# ----------------------------------------------------------------------
def extract_stream(ffmpeg: Optional[str], video_path: str,
                   stream_index: int, out_path: Path) -> bool:
    """把内嵌封面那条流抽成 jpg (压到长边 <=448、保持比例)。成功返回 True。"""
    if not ffmpeg or not video_path or not os.path.isfile(video_path):
        return False
    tmp = _tmp_out(out_path)
    try:
        _run([
            ffmpeg, "-v", "error", "-y",
            "-i", video_path,
            "-map", f"0:{int(stream_index)}",
            "-frames:v", "1",
            "-vf", _VF_NORMALIZE,
            "-q:v", _QV,
            str(tmp),
        ], EXTRACT_TIMEOUT)
    except (subprocess.SubprocessError, OSError):
        return False
    # 以文件为准: ffmpeg 有时返回 0 却什么都没写
    return _finalize(tmp, out_path)


def extract_frame(ffmpeg: Optional[str], video_path: str,
                  timestamp: float, out_path: Path) -> bool:
    """
    在指定时间点截一帧当封面。

    `-ss` 放在 `-i` **前面** = 快速定位(按关键跳), 放后面要解码整段,
    对 4K 长片差着几十倍。
    """
    if not ffmpeg or not video_path or not os.path.isfile(video_path):
        return False
    ts = max(0.0, float(timestamp))
    tmp = _tmp_out(out_path)
    try:
        _run([
            ffmpeg, "-v", "error", "-y",
            "-ss", f"{ts:.3f}", "-i", video_path,
            "-frames:v", "1",
            "-vf", _VF_NORMALIZE,
            "-q:v", _QV,
            str(tmp),
        ], EXTRACT_TIMEOUT)
    except (subprocess.SubprocessError, OSError):
        return False
    return _finalize(tmp, out_path)


# ----------------------------------------------------------------------
# 黑屏检测
# ----------------------------------------------------------------------
def average_brightness(image_path) -> Optional[float]:
    """
    图片的平均亮度 0~255; 读不出来返回 None。

    用 QImage 而不是 QPixmap: QPixmap 必须在 GUI 线程, 而封面是后台线程抽的;
    QImage 是纯 CPU 光栅, 官方就支持在非 GUI 线程用。
    先缩到 16x16 再求均值 —— 平滑缩放本身就是一次面积平均,
    比在 Python 里逐像素遍历 14 万个点快得多。
    """
    img = QImage(str(image_path))
    if img.isNull():
        return None
    small = img.scaled(16, 16, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    if small.isNull():
        return None
    total = 0
    for y in range(small.height()):
        for x in range(small.width()):
            c = small.pixelColor(x, y)
            total += (c.red() + c.green() + c.blue()) // 3
    n = small.width() * small.height()
    return (total / n) if n else None


def is_dark_image(image_path, threshold: int = DARK_THRESHOLD) -> bool:
    """
    是不是黑屏/坏图。

    ⚠️ 读不出来也算不合格: 截帧"成功"但图片解码不了, 同样不能当封面用。
    片头 10% 经常是黑场或片商 logo, 所以这一步不能省 ——
    只看"文件生成了没有"会把一张全黑的图当成封面存下来。
    """
    avg = average_brightness(image_path)
    return avg is None or avg < threshold


# ----------------------------------------------------------------------
# 同目录手动海报
# ----------------------------------------------------------------------
def find_manual_poster(video_path: str) -> Optional[Path]:
    """在视频同目录找用户手动放的海报图: poster/cover/folder/front/art 或 <视频同名>。"""
    if not video_path:
        return None
    p = Path(video_path)
    folder = p.parent
    if not folder.is_dir():
        return None

    stem = p.stem.lower()
    wanted = list(_MANUAL_CANDIDATES) + [f"{stem}.{e}" for e in _MANUAL_EXTS]

    try:
        entries = {f.name.lower(): f for f in folder.iterdir() if f.is_file()}
    except OSError:
        return None
    for name in wanted:
        hit = entries.get(name)
        if hit is not None:
            return hit
    return None


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------
def _clamp_timestamps(duration: Optional[float]) -> Sequence[float]:
    """按片长算出要试的时间点, 并做边界保护(别贴着片尾, 也别是负数)。"""
    if not duration or duration <= 0:
        return ()
    last = max(0.0, duration - _SAFETY_MARGIN)
    out = []
    for ratio in FRAME_RATIOS:
        ts = min(duration * ratio, last)
        if ts >= 0 and (not out or ts > out[-1]):
            out.append(ts)
    return tuple(out)


def _try_frames(paths, durations, ffmpeg, out: Path) -> Optional[str]:
    """
    依次在各时间比例上截帧, 每张都过黑屏检测, 第一张合格的就原子落盘。
    返回用了哪个时间点的标签 (如 "frame@10%"), 全失败返回 None。
    """
    for path, dur in zip(paths, durations):
        if dur is None:
            dur = None
        for ratio, ts in zip(FRAME_RATIOS, _clamp_timestamps(dur)):
            if extract_frame(ffmpeg, path, ts, out) and not is_dark_image(out):
                return f"frame@{int(round(ratio * 100))}%"
            # 不合格就把残次品删掉, 免得被下一轮当缓存
            try:
                if out.exists():
                    out.unlink()
            except OSError:
                pass
    return None


def ensure_poster(media_id: int, file_paths: Iterable[str],
                  ffprobe: Optional[str], ffmpeg: Optional[str],
                  force: bool = False,
                  durations: Optional[Iterable[Optional[float]]] = None
                  ) -> Tuple[Optional[str], Optional[str]]:
    """
    保证这部作品有一张海报, 返回 (路径, 来源)。
    来源 ∈ cached / manual / embedded / frame@N%, 拿不到则 (None, None)。

    一部作品可能有多个文件 (动漫一季十几集), 挨个试, 第一个成功的就用它。

    `durations` 是与 file_paths 平行的时长列表(秒), 由调用方从库里
    `MediaFile.duration` 直接给 —— 库里已经有了就别再跑一次 ffprobe。
    缺的项传 None, 这里会补探一次。
    """
    out = poster_file(media_id)
    if not force and out.is_file() and out.stat().st_size > 0:
        return str(out), "cached"

    paths = [p for p in (file_paths or []) if p]
    durs = list(durations) if durations is not None else [None] * len(paths)
    durs += [None] * (len(paths) - len(durs))          # 长度对不齐时补齐, 别 zip 截断

    # ① 用户手动放在同目录的图 —— 意图最强, 压过内嵌封面
    for p in paths:
        manual = find_manual_poster(p)
        if manual is None:
            continue
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            # 用复制而不是软链接: 用户可能之后把图片挪走
            out.write_bytes(manual.read_bytes())
            if out.stat().st_size > 0:
                return str(out), "manual"
        except OSError:
            continue
        finally:
            try:
                if out.exists() and out.stat().st_size == 0:
                    out.unlink()
            except OSError:
                pass

    # ② 视频内嵌封面
    for p in paths:
        idx = find_attached_pic(ffprobe, p)
        if idx is not None and extract_stream(ffmpeg, p, idx, out):
            return str(out), "embedded"

    # ③ 自动截帧 (库里没有时长才去问 ffprobe)
    if ffmpeg:
        resolved = []
        for p, d in zip(paths, durs):
            if not d or d <= 0:
                d = get_duration(ffprobe, p)
            resolved.append(d)
        tag = _try_frames(paths, resolved, ffmpeg, out)
        if tag:
            return str(out), tag

    return None, None


class PosterWorker(QThread):
    """给整个库补海报。放在线程里跑: 十几个 ffmpeg 子进程会把 UI 卡住。"""

    poster_started = Signal(int)          # 待处理作品数
    poster_progress = Signal(str, str)    # (标题, 来源: cached/manual/embedded/frame@N%/none/failed)
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
        counts = {"cached": 0, "manual": 0, "embedded": 0,
                  "frame": 0, "none": 0, "failed": 0}
        try:
            with get_session() as s:
                media = s.query(Media).order_by(Media.id).all()
                self.poster_started.emit(len(media))

                for m in media:
                    if self._cancelled:
                        break
                    title = m.title or f"#{m.id}"
                    rows = s.query(MediaFile.file_path, MediaFile.duration).filter(
                        MediaFile.media_id == m.id).all()
                    paths = [r[0] for r in rows if r[0]]
                    durs = [r[1] for r in rows if r[0]]

                    try:
                        path, source = ensure_poster(
                            m.id, paths, self.ffprobe, self.ffmpeg,
                            force=self.force, durations=durs)
                    except Exception as e:      # 单个文件坏了不该中断整批
                        counts["failed"] += 1
                        self.poster_progress.emit(title, "failed")
                        self.poster_error.emit(f"{title}: {type(e).__name__}: {e}")
                        continue

                    if path:
                        m.poster_path = path
                        # frame@10% / frame@30% 都归到 frame 一类计数
                        key = "frame" if str(source).startswith("frame") else source
                        counts[key] = counts.get(key, 0) + 1
                        self.poster_progress.emit(title, source)
                    else:
                        counts["none"] += 1
                        self.poster_progress.emit(title, "none")

                s.commit()
        except Exception as e:
            self.poster_error.emit(f"{type(e).__name__}: {e}")

        self.poster_finished.emit(counts)
