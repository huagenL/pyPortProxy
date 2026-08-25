# -*- mode: python ; coding: utf-8 -*-
"""pyPortProxy 的 PyInstaller 打包配置。

默认单文件无窗口（onefile + windowed）；
设置环境变量 PORTPROXY_ONEDIR=1 时输出目录版（杀软误报时的备选）。

@author ai-lhg
"""

import os
from pathlib import Path

ICON = Path(SPECPATH) / "assets" / "icon.ico"
icon_arg = str(ICON) if ICON.exists() else None
# 托盘图标运行时需读取 ico 文件：一并打入包内（_MEIPASS 根目录）
datas_arg = [(str(ICON), ".")] if ICON.exists() else []

a = Analysis(
    ["main.py"],
    pathex=["src"],
    binaries=[],
    datas=datas_arg,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 保守瘦身：这些标准库模块与本项目无关
    excludes=["unittest", "pydoc_data", "sqlite3", "curses"],
    noarchive=False,
)

pyz = PYZ(a.pure)

onedir = os.environ.get("PORTPROXY_ONEDIR") == "1"

if onedir:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="pyPortProxy",
        debug=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        icon=icon_arg,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        name="pyPortProxy",
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="pyPortProxy",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        icon=icon_arg,
    )
