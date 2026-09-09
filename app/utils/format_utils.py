"""格式化工具 — 把原始数值变成人可读的文本"""


def format_duration(seconds: int) -> str:
    """秒数 → HH:MM:SS (不足 1 小时显示 MM:SS)"""
    if seconds <= 0:
        return "00:00"
    h, r = divmod(int(seconds), 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def format_file_size(size_bytes: int) -> str:
    """字节数 → B/KB/MB/GB/TB 可读大小"""
    if size_bytes <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    idx = 0
    size = float(size_bytes)
    while size >= 1024 and idx < len(units) - 1:
        size /= 1024
        idx += 1
    return f"{size:.2f} {units[idx]}"


def format_resolution(width: int, height: int) -> str:
    """宽高 → 可读分辨率 (附常见档位标注)"""
    if not width or not height:
        return "未知"
    if height >= 2160:
        return f"{width}×{height} (4K)"
    if height >= 1080:
        return f"{width}×{height} (1080p)"
    if height >= 720:
        return f"{width}×{height} (720p)"
    return f"{width}×{height}"