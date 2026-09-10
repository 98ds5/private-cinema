# -*- mode: python ; coding: utf-8 -*-
"""
私人影院 PyInstaller 打包配置。
内嵌 _mpv/ 目录，打出的包开箱即用，无需用户自行安装 mpv。
构建: pyinstaller cinema.spec
"""
from pathlib import Path

datas = []
binaries = []
hiddenimports = []

# 内嵌 mpv (_mpv/ -> _internal/mpv/)
_mpv_dir = Path(__file__).parent / "_mpv"
if _mpv_dir.is_dir():
    for f in _mpv_dir.rglob("*"):
        if f.is_file():
            rel = f.relative_to(_mpv_dir.parent)
            datas.append((str(f), str(rel.parent)))
    print(f"[SPEC] 已内嵌 mpv ({_mpv_dir})")
else:
    print("[SPEC] 警告: _mpv/ 不存在，构建的包不含 mpv")

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