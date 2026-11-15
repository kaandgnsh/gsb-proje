from pathlib import Path
from PyInstaller.utils.hooks import collect_all
root = Path(SPECPATH).parents[1]
datas, binaries, hiddenimports = [], [], []
for package in ('PySide6', 'lai', 'lai_runtime', 'lai_setup'):
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hiddenimports += h
a = Analysis([str(root/'packaging/windows/main.py')], pathex=[str(root/'src')],
             datas=datas, binaries=binaries, hiddenimports=hiddenimports)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='YerelEnvanter', console=True)
coll = COLLECT(exe, a.binaries, a.datas, name='YerelEnvanter')
