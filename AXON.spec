# PyInstaller release definition. Models and user data remain external.
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hidden = collect_submodules("axon.skills")
data = collect_data_files("axon", includes=["visual/web/*.html", "skills/*/*.json"])

a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=[],
    datas=data,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AXON",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="AXON", strip=False, upx=True)
