"""
外部工具路径检测 (ffprobe / mpv)

检测顺序:
  1. 用户在设置页手动指定的自定义路径
  2. 系统 PATH 中的同名命令
  3. 程序同目录下的可执行文件
  4. 常见安装位置

返回 None 表示没找到 — UI 应提示用户手动指定或安装。
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

    ffprobe 找到了但 ffmpeg 不在 PATH 上时, 去它**同目录**再找一次 ——
    官方构建 (含本机那个 WinGet 的 ffmpeg-full) 两个 exe 是放在一起的,
    而用户往往只把其中一个的目录加进了 PATH。
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

    # 4. 常见安装位置 + 本机已知路径
    system = platform.system()
    exe = f"{tool}{suffix}"
    if system == "Windows":
        candidates = [
            f"C:\\Program Files\\{tool}\\bin\\{exe}",
            f"C:\\Program Files (x86)\\{tool}\\bin\\{exe}",
            # mpv-lazy (本机)
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