"""
外部工具路径检测 (ffprobe / ffmpeg / mpv)。
检测顺序: 设置页手动指定 > _tools/ 内嵌目录 > PATH > 同目录 > 常见安装位置。
"""
import sys
import shutil
import platform
from pathlib import Path
from typing import Optional


def _tools_dir() -> Path:
    """获取内嵌工具目录 _tools/。支持源码模式与 PyInstaller 打包模式。"""
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包: _tools/ 在 _internal/ 下
        return Path(sys.executable).parent / "_internal" / "_tools"
    return Path("./_tools")


def detect_ffprobe(custom: Optional[str] = None) -> Optional[str]:
    """检测 ffprobe"""
    return _detect_bundled("ffprobe", custom)


def detect_ffmpeg(custom: Optional[str] = None) -> Optional[str]:
    """检测 ffmpeg (没找到则去 ffprobe 同目录找: 官方构建里两个 exe 放一起)"""
    found = _detect_bundled("ffmpeg", custom)
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
    """检测 mpv"""
    return _detect_bundled("mpv", custom)


def _detect_bundled(tool: str, custom: Optional[str] = None) -> Optional[str]:
    """
    带内嵌目录检测的统一查找:
      1. 自定义路径
      2. _tools/ 内嵌目录 (源码 / PyInstaller)
      3. 系统 PATH
      4. 程序同目录
      5. 常见安装位置
    """
    # 1. 自定义路径
    if custom and custom != "auto" and Path(custom).is_file():
        return custom

    # 2. 内嵌 _tools/ 目录
    suffix = ".exe" if platform.system() == "Windows" else ""
    bundled = _tools_dir() / f"{tool}{suffix}"
    if bundled.is_file():
        return str(bundled.resolve())

    # 3. 系统 PATH
    found = shutil.which(tool)
    if found:
        return found

    # 4. 程序同目录
    local = Path(f"./{tool}{suffix}")
    if local.is_file():
        return str(local.resolve())

    # 5. 常见安装位置
    system = platform.system()
    exe = f"{tool}{suffix}"
    if system == "Windows":
        candidates = [
            f"C:\\Program Files\\{tool}\\bin\\{exe}",
            f"C:\\Program Files (x86)\\{tool}\\bin\\{exe}",
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