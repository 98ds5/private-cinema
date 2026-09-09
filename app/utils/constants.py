"""全局常量"""

# 支持的视频扩展名
# 前 9 种对应需求文档 3.1.2; 后 3 种 (.wmv/.iso/.vob) 为实际片源补充:
#   .wmv — Windows 常见封装
#   .iso — 原盘镜像, mpv / ffprobe 均可直接读取
#   .vob — DVD 视频对象
# 扫描与解析共用此列表, 新增格式只需改这一处。
VIDEO_EXTENSIONS = [
    ".mkv", ".mp4", ".m4v", ".avi", ".mov",
    ".webm", ".ts", ".m2ts", ".flv",
    ".wmv", ".iso", ".vob",
]