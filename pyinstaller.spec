# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for photo-manager — --onedir Windows bundle.

Built by .github/workflows/release.yml on tag push (v*.*.*) and
locally via `pyinstaller pyinstaller.spec --clean --noconfirm`.

Reproducibility:
- No machine-specific absolute paths. SPECPATH resolves to the
  directory holding this spec at build time (set automatically by
  PyInstaller), keeping the spec portable across machines.
- Hidden-imports and add-binary entries for pillow-heif and rawpy
  are driven by their published packaging quirks, not guesses — see
  the comments next to each block before editing.

Iteration policy: refine the `excludes` list from real PyInstaller
WARNINGs + the smoke step's stderr, never from speculation. The
starter list is a generous trim aimed at PySide6's optional Qt
modules and dev-only stdlib; entries that turn out to be required
will show up as missing-module errors at runtime.
"""

from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs

# pillow-heif ships a compiled extension plus libheif/libde265/etc
# native DLLs. collect_all picks up the Python package, data files,
# and binaries in one call — the documented "just works" path for
# this package.
heif_datas, heif_binaries, heif_hiddenimports = collect_all("pillow_heif")

# rawpy bundles libraw.dll under rawpy/libraw_*.dll on Windows.
# collect_dynamic_libs is the documented helper for grabbing the
# DLL without dragging in the entire site-packages tree.
rawpy_binaries = collect_dynamic_libs("rawpy")

block_cipher = None


a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=heif_binaries + rawpy_binaries,
    datas=heif_datas + [
        # Bundled read-only assets resolved via sys._MEIPASS / BASE_DIR
        # in main.py. translations/ holds the YAML catalogs the i18n
        # layer reads at startup. No icons/PNGs are loaded by the app
        # today (verified by grep) so only translations/ is bundled.
        ("translations", "translations"),
    ],
    hiddenimports=heif_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # PySide6 — optional modules the app doesn't import. Trims
        # tens of MB. Refine from PyInstaller WARNINGs if any of these
        # turn out to be transitively required.
        "PySide6.Qt3DCore",
        "PySide6.Qt3DRender",
        "PySide6.Qt3DInput",
        "PySide6.Qt3DLogic",
        "PySide6.Qt3DAnimation",
        "PySide6.Qt3DExtras",
        "PySide6.QtWebEngine",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebView",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
        "PySide6.QtNetworkAuth",
        "PySide6.QtBluetooth",
        "PySide6.QtNfc",
        "PySide6.QtPositioning",
        "PySide6.QtLocation",
        "PySide6.QtSerialPort",
        "PySide6.QtSerialBus",
        "PySide6.QtSensors",
        "PySide6.QtTextToSpeech",
        "PySide6.QtRemoteObjects",
        "PySide6.QtScxml",
        "PySide6.QtSql",
        "PySide6.QtTest",
        "PySide6.QtHelp",
        "PySide6.QtDesigner",
        # Stdlib modules pulled in by transitive deps but never used
        # by the app's runtime path.
        "tkinter",
        "unittest",
        "pydoc",
        "doctest",
        "pdb",
        "bdb",
        # Dev / test tooling — present in the build venv via
        # requirements.txt? No: requirements.txt is runtime-only.
        # Listed defensively in case a transitive dep imports them.
        "pip",
        "setuptools",
        "wheel",
        "pytest",
        "_pytest",
        "coverage",
        "pylint",
        "mypy",
        "black",
        "isort",
        "ruff",
        "jupyter",
        "IPython",
        "matplotlib",
        # NOTE: scipy is NOT excluded — imagehash uses scipy.fftpack
        # for phash() (verified: `import imagehash` source references
        # scipy). Excluding it silently breaks deduplication scanning.
        # rawpy itself doesn't need scipy, but imagehash does.
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="photo-manager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX often triggers AV false positives on Windows;
                # SmartScreen unhappiness is already enough friction.
    console=False,  # GUI app — no console window on launch.
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="photo-manager",
)
