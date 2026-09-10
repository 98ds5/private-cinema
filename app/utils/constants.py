"""全局常量"""

# 支持的视频扩展名 (扫描与解析共用此列表, 新增格式只改这一处)。
# .wmv/.iso/.vob 是补充项: Windows 封装 / 原盘镜像 (mpv、ffprobe 可直接读) / DVD 视频对象。
VIDEO_EXTENSIONS = [
    ".mkv", ".mp4", ".m4v", ".avi", ".mov",
    ".webm", ".ts", ".m2ts", ".flv",
    ".wmv", ".iso", ".vob",
]