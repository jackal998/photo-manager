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

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs

# The web shell serves the built SPA from inside the bundle; a missing
# build here would otherwise surface only as a dead page at runtime.
# Fail the BUILD instead (#772).
_frontend_dist = Path(SPECPATH) / "frontend" / "dist"
if not (_frontend_dist / "index.html").exists():
    raise SystemExit(
        "frontend/dist/index.html not found — build the frontend first "
        "(cd frontend && npm ci && npm run build). The release bundle "
        "ships the built SPA; PyInstaller must not run without it."
    )

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
    # launcher.py dispatches: Qt desktop by default, web shell when
    # PHOTO_MANAGER_WEB is truthy, server-only smoke when
    # PHOTO_MANAGER_WEB_SMOKE is truthy (#772). The old main.py Qt
    # entry is what launcher's default path runs, so classic behaviour
    # is unchanged.
    ["launcher.py"],
    pathex=[],
    binaries=heif_binaries + rawpy_binaries,
    datas=heif_datas + [
        # Bundled read-only assets resolved via sys._MEIPASS / BASE_DIR
        # in main.py. translations/ holds the YAML catalogs the i18n
        # layer reads at startup. No icons/PNGs are loaded by the app
        # today (verified by grep) so only translations/ is bundled.
        ("translations", "translations"),
        # Built SPA for the web shell. app/web/main.py resolves
        # Path(__file__).parents[2]/frontend/dist, which under a frozen
        # onedir build is <_internal>/frontend/dist — exactly this dest.
        ("frontend/dist", "frontend/dist"),
    ],
    hiddenimports=heif_hiddenimports + [
        # pywebview's Windows backends are selected at runtime by string,
        # invisible to static analysis. winforms is the .NET host window,
        # edgechromium the WebView2 embedding; clr_loader/pythonnet are
        # the .NET bridge both ride on.
        "webview.platforms.winforms",
        "webview.platforms.edgechromium",
        "clr_loader",
        "pythonnet",
        # uvicorn resolves its event loop / protocol / lifespan classes
        # from config STRINGS (uvicorn/config.py), so static analysis
        # misses them. The .auto modules then import their concrete
        # siblings dynamically as well.
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.loops.asyncio",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.http.httptools_impl",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan.on",
    ],
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
