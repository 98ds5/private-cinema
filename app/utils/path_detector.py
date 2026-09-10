"""
外部工具路径检测 (ffprobe / mpv)。
检测顺序: 设置页手动指定、系统 PATH、程序同目录、常见安装位置; 找不到返回 None。
"""
import shutil
import platform
from pathlib import Path
from typing import Optional


def detect_ffprobe(custom: Optional[str] = None) -> Optional[str]:
    """检测 ffprobe; custom 为设置里手动填写的路径 ('auto' 表示不指定)"""
    return _detect("ffprobe", custom)


def detect_ffmpeg(custom: Optional[str] = None) -> Optional[str]:
    """
    ffmpeg 可执行文件 (抽内嵌封面用)。
    ffprobe 找到但 ffmpeg 不在 PATH 上时, 去它同目录再找一次: 官方构建里两个 exe 放一起。
    """
    found = _detect("ffmpeg", custom)
    if found:
        return found
    probe = detect_ffprobe(None)
    if probe:
        exe = "ffmpeg.exe" if platform.system() == "Windows" else "ffmpeg"
        sibling = Path(probe).with_name(exe)
        if sibling.is_file():
            return str(sibling)
    return None


def detect_mpv(custom: Optional[str] = None) -> Optional[str]:
    """检测 mpv; custom 为设置里手动填写的路径 ('auto' 表示不指定)"""
    return _detect("mpv", custom)


def _detect(tool: str, custom: Optional[str] = None) -> Optional[str]:
    # 1. 自定义路径
    if custom and custom != "auto" and Path(custom).is_file():
        return custom

    # 2. 系统 PATH
    found = shutil.which(tool)
    if found:
        return found

    # 3. 程序同目录
    suffix = ".exe" if platform.system() == "Windows" else ""
    local = Path(f"./{tool}{suffix}")
    if local.is_file():
        return str(local.resolve())

    # 4. 常见安装位置
    system = platform.system()
    exe = f"{tool}{suffix}"
    if system == "Windows":
        candidates = [
            f"C:\\Program Files\\{tool}\\bin\\{exe}",
            f"C:\\Program Files (x86)\\{tool}\\bin\\{exe}",
            # mpv 懒人包解压目录
            f"D:\\Movie\\mpv\\mpv-lazy\\{exe}",
        ]
    elif system == "Darwin":
        candidates = [
            f"/usr/local/bin/{tool}",
            f"/opt/homebrew/bin/{tool}",
        ]
    else:
        candidates = [
            f"/usr/bin/{tool}",
            f"/usr/local/bin/{tool}",
        ]

    for p in candidates:
        if Path(p).is_file():
            return p

    return None