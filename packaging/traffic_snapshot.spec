# PyInstaller specification — builds a self-contained Windows folder
# (dist/TrafficSnapshot/TrafficSnapshot.exe) that runs without Python.
# Build with:  pyinstaller packaging/traffic_snapshot.spec --noconfirm
from pathlib import Path

ROOT = Path(SPECPATH).parent
models = [(str(p), "models") for p in (ROOT / "models").glob("*.onnx")]
if not models:
    print("WAARSCHUWING: geen modellen in models/ — voer eerst scripts/fetch_models.py uit.")

a = Analysis(
    [str(ROOT / "traffic_snapshot" / "__main__.py")],
    pathex=[str(ROOT)],
    datas=models + [(str(ROOT / "traffic_snapshot" / "resources"), "traffic_snapshot/resources")],
    hiddenimports=["matplotlib.backends.backend_qtagg", "PySide6.QtPdf"],
    excludes=["tkinter", "PyQt5", "PyQt6", "IPython", "pytest", "PySide6.QtWebEngineCore",
              "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore", "PySide6.QtQuick", "PySide6.QtQml"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TrafficSnapshot",
    icon=str(ROOT / "packaging" / "app.ico"),
    console=False,
    upx=False,
    version=str(ROOT / "packaging" / "version_info.txt"),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="TrafficSnapshot")
