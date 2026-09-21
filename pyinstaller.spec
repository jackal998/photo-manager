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
list trims dev-only stdlib and tooling; entries that turn out to be
required will show up as missing-module errors at runtime.
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

# Bundled ffmpeg/ffprobe (#854).  release.yml stages a checksum-verified
# LGPL build into build-assets/ffmpeg/ BEFORE this spec runs; a dev build
# usually has no such directory and simply ships without them (the app then
# falls back to PATH, which is the pre-#854 behaviour).  Missing binaries
# are therefore a warning here, never a build failure — unlike
# frontend/dist above, whose absence produces a visibly dead app.
#
# The build is the SHARED LGPL variant: two small exes plus their av*/sw*
# DLLs (~128 MB) instead of two ~114 MB statically linked exes (~218 MB).
# Everything staged is shipped, whatever the staging step put there.
#
# Dest "ffmpeg" — a sub-directory, NOT loose in _internal/, and measured
# on a local build of this spec: the shared build's exes import their
# av*/sw* DLLs by name, and Windows searches the exe's OWN directory
# first. Keeping the set in one directory is what makes ffmpeg.exe load
# the libraries it was built against, wherever the bundle is unpacked.
# transcode_service.py's _resolve_media_tool() looks in <_MEIPASS>/ffmpeg
# and <exe dir>/ffmpeg before the loose locations.
#
# datas, not binaries: these files are a self-consistent set that must be
# copied verbatim into one directory. PyInstaller's binary analysis would
# hoist the DLLs it recognises into _internal/, splitting the set across
# _internal/ and _internal/ffmpeg/ — which is exactly what this layout
# exists to prevent.
#
# The staging step names the licence FFMPEG-LICENSE.txt so it cannot be
# mistaken for the app's own once it sits in the bundle; a datas entry
# copies files verbatim and cannot rename them.
_ffmpeg_stage = Path(SPECPATH) / "build-assets" / "ffmpeg"
_ffmpeg_required = ("ffmpeg.exe", "ffprobe.exe", "FFMPEG-LICENSE.txt")
ffmpeg_datas = []
if (_ffmpeg_stage / "ffmpeg.exe").exists():
    for _staged in sorted(_ffmpeg_stage.iterdir()):
        if _staged.is_file():
            ffmpeg_datas.append((str(_staged), "ffmpeg"))
    _staged_names = {Path(src).name for src, _ in ffmpeg_datas}
    _missing = [n for n in _ffmpeg_required if n not in _staged_names]
    if _missing:
        raise SystemExit(
            f"build-assets/ffmpeg/ is incomplete — missing {_missing}. "
            "A half-staged directory would ship an ffmpeg that cannot run; "
            "re-run the fetch/verify/extract step (#854)."
        )
    print(
        f"pyinstaller.spec: bundling {len(ffmpeg_datas)} ffmpeg files into "
        f"_internal/ffmpeg ({sum(Path(s).stat().st_size for s, _ in ffmpeg_datas)} bytes)"
    )
else:
    print(
        f"pyinstaller.spec: {_ffmpeg_stage} has no ffmpeg.exe — bundle will "
        "ship without ffmpeg; video transcoding will need it on PATH (#854)"
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
    # launcher.py is the only entry point since the desktop client was
    # removed (#646): it opens the web shell unconditionally, or runs the
    # server-only smoke when PHOTO_MANAGER_WEB_SMOKE is truthy (#772,
    # release.yml's web-shell smoke step).
    ["launcher.py"],
    pathex=[],
    binaries=heif_binaries + rawpy_binaries,
    datas=heif_datas + ffmpeg_datas + [
        # Bundled read-only assets, resolved under sys._MEIPASS at
        # runtime (writable state goes next to the exe instead — see
        # infrastructure/settings.py, #882). translations/ holds the YAML
        # catalogs the i18n layer reads at startup. No icons/PNGs are
        # loaded by the app today (verified by grep) so only
        # translations/ is bundled.
        ("translations", "translations"),
        # Built SPA for the web shell. `app.web.main` resolves
        # Path(__file__).parents[2]/frontend/dist, which under a frozen
        # onedir build is <_internal>/frontend/dist — exactly this dest.
        ("frontend/dist", "frontend/dist"),
    ],
    hiddenimports=heif_hiddenimports + [
        # launcher.py hands uvicorn the app as the STRING
        # "app.web.main:create_app" (uvicorn.Config(..., factory=True)), so
        # static analysis never sees the web app and PyInstaller collected
        # no `app/` package at all. Measured on a local build of this spec
        # BEFORE this entry: the frozen exe's own web smoke
        # (PHOTO_MANAGER_WEB_SMOKE=1) exits 1 with
        # "ModuleNotFoundError: No module named 'app.web'" — i.e. the
        # packaged web shell could not start, which also makes the bundled
        # ffmpeg (#854) unreachable. Pulling in the factory module drags its
        # static imports (routes, services) with it.
        "app.web.main",
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

# PyInstaller reclassifies our staged PE files as binaries (they leave
# `datas` and arrive in `a.binaries` keeping the "ffmpeg\..." destination)
# and its dependency analysis ALSO adds each discovered DLL a SECOND time
# at the top level of _internal/. Measured on local builds: without this
# filter every av*/sw* DLL shipped twice — _internal/ and _internal/ffmpeg/
# — costing 128 MB of duplication and putting the loose copies exactly
# where this layout exists to keep them out of.
#
# So the filter keys on the DESTINATION, not the source: drop an entry only
# when it is one of our staged files AND it is headed for the top level.
# (Filtering by source alone deletes the correctly-placed copies too — that
# build shipped an _internal/ffmpeg holding nothing but the licence.)
if ffmpeg_datas:
    _staged_sources = {str(Path(src).resolve()).lower() for src, _ in ffmpeg_datas}
    _binaries_before = len(a.binaries)
    a.binaries = [
        _entry for _entry in a.binaries
        if not (
            str(Path(_entry[1]).resolve()).lower() in _staged_sources
            and Path(_entry[0]).parent == Path(".")
        )
    ]
    print(
        f"pyinstaller.spec: dropped {_binaries_before - len(a.binaries)} "
        "top-level duplicates so the ffmpeg set ships once, in _internal/ffmpeg"
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
