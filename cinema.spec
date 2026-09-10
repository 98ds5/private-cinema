# -*- mode: python ; coding: utf-8 -*-
"""
私人影院 PyInstaller 打包配置。
内嵌 _tools/ 目录 (mpv / ffmpeg / ffprobe)，打出的包开箱即用。
构建: pyinstaller cinema.spec
"""
from pathlib import Path

datas = []
binaries = []
hiddenimports = []

# 内嵌工具集 (_tools/ -> _internal/_tools/)
_tools_dir = Path(SPECPATH) / "_tools"
if _tools_dir.is_dir():
    for f in _tools_dir.rglob("*"):
        if f.is_file():
            rel = f.relative_to(_tools_dir.parent)
            datas.append((str(f), str(rel.parent)))
    print(f"[SPEC] 已内嵌工具集 ({_tools_dir})")
else:
    print("[SPEC] 警告: _tools/ 不存在，构建的包不含外部工具")

# conda 的 _ctypes.pyd / pyexpat.pyd 链接 ffi.dll / libexpat.dll（conda 特有命名），
# PyInstaller 自动解析不到，需手动内嵌到 _internal\ 根。
_venv_bin = Path(SPECPATH) / "cinema_env" / "Library" / "bin"
for _f in ("ffi.dll", "libexpat.dll"):
    _p = _venv_bin / _f
    if _p.is_file():
        binaries.append((str(_p), "."))

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

# ---------------------------------------------------------------------------
# 关键修复: 剔除 conda 环境带来的旧版 ICU (icuuc.dll / icudt58.dll, ICU 58)。
# PySide6 6.8+ 的 Qt6Core.dll 导入**无版本后缀**的 ucnv_open 等符号，
# 这些由 Windows 10 1703+ 自带的 System32\icuuc.dll(转发桩)提供。
# 若把 conda 的 icuuc.dll 打进 _internal\，加载器会优先搜 _internal\，
# 旧 ICU 只有 ucnv_open_58 这类带后缀导出 -> ERROR_PROC_NOT_FOUND(127)
# -> "DLL load failed while importing QtWidgets: 找不到指定的程序"。
# ---------------------------------------------------------------------------
import os as _os
def _is_bundled_icu(item):
    name = _os.path.basename(item[0]).lower()
    return name == 'icuuc.dll' or name.startswith(('icudt', 'icuuc', 'icuin', 'icudata'))
_removed = [b for b in a.binaries if _is_bundled_icu(b)]
if _removed:
    print(f"[SPEC] 剔除旧版 ICU: {[ _os.path.basename(b[0]) for b in _removed]}")
a.binaries = [b for b in a.binaries if not _is_bundled_icu(b)]

# 同理强制用 conda 的 sqlite3.dll（PyInstaller 收集到的是旧版，缺 sqlite3_serialize）
_sq = str(_venv_bin / "sqlite3.dll")
a.binaries = [b for b in a.binaries if _os.path.basename(b[0]).lower() != 'sqlite3.dll']
a.binaries.append(('sqlite3.dll', _sq, 'BINARY'))
# ---------------------------------------------------------------------------

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='私人影院',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='私人影院',
)