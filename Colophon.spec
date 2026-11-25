# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：onedir 绿色包。

    python -m PyInstaller --noconfirm --clean Colophon.spec

产物在 dist/Colophon/；入口和开发时一样是 run.py，所以双击 exe 与
`python run.py` 走同一条代码路径。把整个目录压缩发给同事即可。
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH)

datas = [
    (str(ROOT / "web"), "web"),
    (str(ROOT / "docs"), "docs"),
    (str(ROOT / "app" / "schema.sql"), "app"),
]

# pymupdf 在 app/metadata.py 里是函数内导入，PyInstaller 扫不到，
# 需要显式收集原生库；pypdf 同理但更简单。
binaries = []
hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "webview.platforms.winforms",
    "webview.platforms.edgechromium",
]

for package in ("pymupdf", "pypdf", "webview"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

a = Analysis(
    [str(ROOT / "run.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "numpy",
        "pandas",
        "IPython",
        "pytest",
        "setuptools",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Colophon",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=str(ROOT / "web" / "favicon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Colophon",
)
