"""HEVC-to-H.264 transcode service for web video fallback.

Where ffmpeg comes from (#854): a packaged release BUNDLES a pinned LGPL
ffmpeg + ffprobe (``.github/workflows/release.yml`` downloads and
checksum-verifies them, ``pyinstaller.spec`` puts them in the bundle), and
the service prefers that copy over anything on PATH — before #854 the
shipped app resolved neither and every HEVC video answered HTTP 501.  A dev
checkout has no bundled copy and still uses PATH.  One consequence drives
the encoder choice below: an LGPL build is built ``--disable-libx264``
(x264 is GPL), so the encoder is picked from what the resolved binary
actually offers instead of being hard-coded.

Qt-free: shells out to ffmpeg via subprocess.  The only consumer is the
FastAPI media route (app/web/routes/media.py) which calls
``get_transcoded_path`` via ``run_in_executor`` so the blocking ffmpeg
sub-process never touches the event loop.

Codec passthrough (#853): nothing else in the pipeline knows a video's
codec, so before #853 every source reaching this service was re-encoded
with libx264 — including the H.264 ``.mov`` files an iPhone library is
full of, which only got here because the #787 pre-check has to guess from
the file EXTENSION.  On a cache miss the service now asks ffprobe once
what the source actually holds; when it is already 8-bit 4:2:0 H.264
with browser-playable audio the cached MP4 is produced by a stream COPY
(remux) instead of an encode: same bytes for the video track, no quality
loss, no encode stall.  The pixel format is part of the verdict because
``codec_name == "h264"`` also covers High 10 / 4:2:2 / 4:4:4, which no
mainstream browser decodes — and for the same reason the encode now
forces ``-pix_fmt yuv420p`` rather than inheriting the source's.

Why a remux and not "serve the original bytes": the cache artifact stays
a plain MP4 that every engine can demux.  Handing back the source
container instead would break the files it aims to help — ``.avi`` is a
walked video extension (scanner/media.py) and no browser demuxes AVI,
and Firefox does not demux QuickTime, so an H.264 ``.mov`` served raw
fails there while today's encode works.  The frontend's two-attempt swap
cannot recover from that: the source it swaps to is byte-identical to the
one that just failed.

Cache design mirrors image_service.py:
- Versioned sub-directory under the configured base dir.
- Cache key = sha1(source_path | mtime_ns).hexdigest() + ".mp4".
- Atomic write: transcode to <key>.mp4.tmp, os.replace → <key>.mp4 on
  success; delete .tmp on failure so no half-written file survives.
- Two concurrency guards:
  (a) Per-cache-key Lock: serialises concurrent requests for the SAME
      source so the second caller finds the cache hit after the first
      finishes, never re-transcodes.
  (b) Module-level BoundedSemaphore(_TRANSCODE_SEM): limits the total
      number of concurrent MEDIA sub-processes — ffmpeg and the #853
      ffprobe alike (#862) — to 2.  Acquired only around each
      subprocess call; released in a finally block.  Cache hits bypass
      both guards.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional

from loguru import logger

# Bump to invalidate the on-disk cache namespace.
TRANSCODE_RECIPE_VERSION = "1"

# Hard cap on concurrent media sub-processes: ffmpeg encodes/remuxes and
# the #853 ffprobe verdict share it (#862).
_TRANSCODE_SEM = threading.BoundedSemaphore(2)

# Per-cache-key locks: serialise concurrent requests for the same source.
# The outer lock protects _KEY_LOCKS itself; inner locks protect individual
# transcode operations.
_KEY_LOCKS_LOCK = threading.Lock()
_KEY_LOCKS: dict[str, threading.Lock] = {}

# Mirror exif.py's _CREATE_NO_WINDOW constant.
_CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

# Codec passthrough (#853).  A source matching ALL THREE of these can be
# remuxed into MP4 with -c copy instead of re-encoded: every target
# browser decodes 8-bit 4:2:0 H.264 video, and AAC/MP3 are the audio
# codecs that are both browser-playable and legal in an MP4 container.
# Anything else (HEVC, VP9, AV1, PCM/AC-3/Opus audio, …) keeps the
# libx264 encode.
_COPYABLE_VIDEO_CODEC = "h264"
_COPYABLE_AUDIO_CODECS = frozenset({"aac", "mp3"})

# `codec_name == "h264"` is NOT enough to promise a browser can decode it.
# H.264 High 10 / High 4:2:2 / High 4:4:4 (10-bit and 4:2:2 output from
# Sony and Panasonic bodies, and from some screen recorders) is still
# "h264" to ffprobe, and no mainstream browser decodes those profiles —
# copying one through would hand the <video> element a file it refuses,
# where the pre-#853 encode at least produced something.  The pixel
# format is the cheapest reliable proxy for the profile, and it comes
# from the same single probe.  `yuvj420p` is the same 8-bit 4:2:0 data
# with full-range flags, which browsers handle.
_COPYABLE_PIX_FMTS = frozenset({"yuv420p", "yuvj420p"})

# ffprobe only reads container headers, so this is generous even for a
# cold file on a NAS.  Bounded so a wedged probe can never hold the
# per-key lock (and therefore the request) open indefinitely.
_PROBE_TIMEOUT_S = 30

# Bundled binaries (#854).  A packaged release carries its own ffmpeg and
# ffprobe (release.yml downloads a checksum-pinned LGPL build,
# pyinstaller.spec bundles the two exes plus their shared libraries); a dev
# checkout carries neither and still relies on PATH.
_EXE_SUFFIX = ".exe" if sys.platform == "win32" else ""

# The bundle keeps ffmpeg in its OWN sub-directory rather than loose beside
# the app's other binaries.  The pinned build is the SHARED LGPL variant:
# two small exes plus avcodec-63 / avformat-63 / avutil-61 / swresample-7 /
# swscale-10 / avfilter-12 / avdevice-63, and Windows resolves an exe's
# imports from the exe's own directory first — so the set has to travel
# together.  It also keeps our libraries away from the SECOND FFmpeg DLL
# set this bundle already contains: PySide6 ships avcodec-61 / avformat-61
# / avutil-59 / swresample-5 / swscale-8 for QtMultimedia (measured in a
# local build: PyInstaller puts those in _internal/PySide6/, so nothing
# collides today — this layout is what keeps that true after a soname bump
# on either side).
_BUNDLE_SUBDIR = "ffmpeg"

# `ffmpeg -encoders` on the bundled build takes tens of milliseconds; the
# bound exists only so a wedged binary can never hang the first transcode.
_ENCODERS_TIMEOUT_S = 15

# H.264 encoder preference (#854).  libx264 is the historical recipe and
# is what every distro / chocolatey / apt ffmpeg carries — it stays first
# so dev machines and CI behave exactly as before.  The bundled build is
# LGPL, which means `--disable-libx264` (x264 is GPL); its software H.264
# encoder is Cisco's libopenh264, whose output is plain 8-bit 4:2:0 H.264
# that browsers decode.  Order matters: a build carrying both keeps the
# tuned libx264 recipe.
_H264_ENCODER_PREFERENCE = ("libx264", "libopenh264")

# Used when the encoder probe cannot run (ffmpeg missing at probe time, a
# binary that will not execute, a timeout).  Falling back to libx264
# reproduces the pre-#854 command exactly, so a failed probe can never be
# worse than not probing at all.
_DEFAULT_H264_ENCODER = "libx264"


class TranscodeUnavailable(Exception):
    """Raised when ffmpeg is neither bundled with the app nor on PATH.

    The media route maps this to HTTP 501.
    """


class TranscodeError(Exception):
    """Raised when ffmpeg exits non-zero or the output is missing."""


def _bundled_tool_dirs() -> list[Path]:
    """Directories a PACKAGED build may carry ffmpeg/ffprobe in.

    Empty outside a frozen build: a dev checkout has no bundled binary,
    and probing the interpreter's own directory there would find a
    ``python.exe`` sibling that has nothing to do with this app.

    Four frozen locations, in priority order:

    1. ``<exe dir>/ffmpeg/`` — where a user drops a replacement set
       (binary + its DLLs) without touching the bundle's insides.
    2. ``<_MEIPASS>/ffmpeg/`` — ``_internal/ffmpeg/``, where
       pyinstaller.spec actually puts the shipped build.
    3. ``<exe dir>/`` and 4. ``<_MEIPASS>/`` — the pre-subdirectory
       layout, kept so a hand-placed loose ``ffmpeg.exe`` (the shape the
       README documented first, and the shape a statically linked build
       needs no directory for) still works.
    """
    if not getattr(sys, "frozen", False):
        return []
    roots = [Path(sys.executable).parent]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass))
    return [root / _BUNDLE_SUBDIR for root in roots] + roots


def _resolve_media_tool(name: str) -> Optional[str]:
    """Locate ``name`` ("ffmpeg" / "ffprobe"): bundled first, then PATH.

    The bundled copy wins over PATH deliberately: it is the build this
    project pinned and smoke-tested, whereas a PATH ffmpeg on a user's
    machine is an unknown version with unknown codecs.  Returns ``None``
    when neither exists, which the caller turns into HTTP 501.
    """
    filename = f"{name}{_EXE_SUFFIX}"
    for directory in _bundled_tool_dirs():
        candidate = directory / filename
        if candidate.is_file():
            return str(candidate)
    return shutil.which(name)


def _select_h264_encoder(ffmpeg: str) -> str:
    """Return the H.264 encoder ``ffmpeg`` should be asked for.

    Asks the resolved binary what it actually has rather than assuming:
    the bundled LGPL build has no libx264 at all, so the pre-#854 command
    fails there with "Unknown encoder 'libx264'" — a 500 in place of the
    501 it was meant to fix.  Never raises: any failure answers
    :data:`_DEFAULT_H264_ENCODER`, i.e. the historical command.
    """
    # Deliberately broad.  This probe runs while the web app is starting up,
    # so ANY exception escaping it would take the whole server down over a
    # question whose answer is optional — and the fallback is the exact
    # command this module used before #854, so swallowing costs nothing.
    # Not hypothetical: it first ran during the FastAPI lifespan in a test
    # that had replaced subprocess.Popen with a stub, and the resulting
    # TypeError (neither OSError nor SubprocessError) broke create_app().
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-encoders"],
            shell=False,
            timeout=_ENCODERS_TIMEOUT_S,
            check=False,
            capture_output=True,
            creationflags=_CREATE_NO_WINDOW,
        )
        if result.returncode != 0:
            logger.warning(
                "ffmpeg -encoders exited {} — assuming {}",
                result.returncode, _DEFAULT_H264_ENCODER,
            )
            return _DEFAULT_H264_ENCODER

        listing = result.stdout.decode("utf-8", errors="replace")
        # The listing is one encoder per line as " V....D libx264   H.264 …";
        # match on the whitespace-delimited name so "libx264rgb" (a different
        # encoder, RGB-only) can never be mistaken for "libx264".
        available = set()
        for line in listing.splitlines():
            fields = line.split()
            if len(fields) >= 2:
                available.add(fields[1])
    except Exception as exc:  # noqa: BLE001 - see comment above
        logger.warning(
            "Could not list ffmpeg encoders ({}) — assuming {}",
            exc, _DEFAULT_H264_ENCODER,
        )
        return _DEFAULT_H264_ENCODER

    for candidate in _H264_ENCODER_PREFERENCE:
        if candidate in available:
            return candidate

    logger.warning(
        "ffmpeg has none of {} — transcodes will fail until one is present",
        ", ".join(_H264_ENCODER_PREFERENCE),
    )
    return _DEFAULT_H264_ENCODER


def _key_lock(cache_key: str) -> threading.Lock:
    """Return (creating if needed) the per-key Lock for ``cache_key``."""
    with _KEY_LOCKS_LOCK:
        if cache_key not in _KEY_LOCKS:
            _KEY_LOCKS[cache_key] = threading.Lock()
        return _KEY_LOCKS[cache_key]


def _compute_cache_key(source: Path) -> str:
    """Compute sha1(source_path | mtime_ns).hexdigest().

    mtime_ns invalidates the cache when the file changes on disk.
    """
    mtime_ns = source.stat().st_mtime_ns
    sig = f"{source}|{mtime_ns}".encode("utf-8", errors="ignore")
    return hashlib.sha1(sig).hexdigest()


def _parse_probe_streams(stdout: bytes) -> Optional[list[dict]]:
    """Parse ffprobe's ``-print_format json`` stdout into a stream list.

    Returns ``None`` — never raises — when the output is not the shape
    this module expects (empty, not JSON, no ``streams`` array, or an
    array holding something other than objects).  ``None`` means "no
    verdict", which the caller turns into a re-encode.
    """
    try:
        # errors="replace" cannot raise, so ValueError here means
        # "ffprobe did not emit JSON" (empty output included).
        payload = json.loads(stdout.decode("utf-8", errors="replace"))
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    streams = payload.get("streams")
    if not isinstance(streams, list):
        return None
    if not all(isinstance(s, dict) for s in streams):
        return None
    return streams


class TranscodeService:
    """Lazy, cached HEVC-to-H.264 transcoder.

    Construct once in the FastAPI lifespan and store on
    ``app.state.transcode_service``.  All public methods are blocking —
    callers on the async path MUST use ``run_in_executor``.
    """

    def __init__(self, settings: Optional[object] = None) -> None:
        """Initialise cache directory and locate ffmpeg.

        Parameters
        ----------
        settings:
            A ``JsonSettings`` instance (or duck-typed equivalent).  Used
            to read ``video_transcode_cache_dir`` the same way
            ImageService reads ``thumbnail_disk_cache_dir``.  May be
            ``None`` (e.g. in tests) — the default platform path is used.
        """
        default_dir = str(
            Path.home() / "AppData" / "Local" / "PhotoManager" / "transcodes"
        )
        if settings is not None:
            raw = settings.get("video_transcode_cache_dir", default_dir)
            if isinstance(raw, str):
                default_dir = os.path.expandvars(raw)

        self._base_dir = Path(default_dir) / f"v{TRANSCODE_RECIPE_VERSION}"
        self._base_dir.mkdir(parents=True, exist_ok=True)

        # Resolve ffmpeg once at init: the binary bundled with a packaged
        # release first, then PATH (#854).  None means "nowhere to be found".
        self._ffmpeg: Optional[str] = _resolve_media_tool("ffmpeg")
        if self._ffmpeg is None:
            logger.warning(
                "ffmpeg not found next to the app or on PATH — "
                "transcode fallback unavailable"
            )
        else:
            logger.info("ffmpeg resolved to {}", self._ffmpeg)

        # ffprobe ships beside ffmpeg and is resolved the same way (#853).
        # Absent is not an error: without it every source is re-encoded,
        # which is exactly the pre-#853 behaviour.
        self._ffprobe: Optional[str] = _resolve_media_tool("ffprobe")
        if self._ffprobe is None:
            logger.warning(
                "ffprobe not found next to the app or on PATH — codec "
                "passthrough disabled, every transcode will re-encode"
            )

        # Which H.264 encoder THIS ffmpeg has (#854).  Decided here, with
        # the binary itself: everything about the ffmpeg this service will
        # use is pinned at init, and the ~40 ms probe is paid once per
        # process rather than on the first (already slow) transcode.
        self._h264_encoder: str = (
            _select_h264_encoder(self._ffmpeg)
            if self._ffmpeg is not None
            else _DEFAULT_H264_ENCODER
        )
        logger.info("H.264 encoder for transcodes: {}", self._h264_encoder)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_transcoded_path(self, source: Path) -> Path:
        """Return the path to the cached H.264 MP4 for ``source``.

        Transcodes on cache-miss — or, when ffprobe says the source is
        already H.264 with browser-playable audio, remuxes it with a
        stream copy instead of re-encoding (#853).  Either way the
        returned path is a cached MP4, so callers see no difference.
        Raises:
        - ``TranscodeUnavailable`` if ffmpeg is neither bundled nor on PATH.
        - ``TranscodeError`` if ffmpeg exits non-zero or output is
          missing after a successful transcode.
        - ``OSError`` if the source file cannot be stat'd.

        Thread-safe: two concurrent requests for the same ``source``
        serialise on the per-key Lock; the second caller returns the
        cache hit without re-transcoding.  The total number of
        concurrent media sub-processes — ffmpeg and ffprobe together
        (#862) — is capped at 2.
        """
        if self._ffmpeg is None:
            raise TranscodeUnavailable(
                "ffmpeg not found next to the app or on PATH; "
                "cannot transcode video"
            )

        cache_key = _compute_cache_key(source)
        out_path = self._base_dir / f"{cache_key}.mp4"

        lock = _key_lock(cache_key)
        with lock:
            # Fast path: cache hit (checked inside the key-lock so a
            # concurrent first-call can't race past the rename).
            if out_path.exists():
                return out_path

            self._transcode(source, out_path)
            return out_path

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _transcode(self, source: Path, out_path: Path) -> None:
        """Produce ``out_path`` from ``source`` with ffmpeg.

        Two recipes, chosen by one ffprobe call (#853): a source that is
        already H.264 with browser-playable audio is REMUXED (stream
        copy) into MP4; everything else is re-encoded with libx264 as
        before.  A remux that fails for any reason falls through to the
        encode, so the passthrough can never make a file less playable
        than it was before #853.

        This method only ever runs on a cache miss, inside the per-key
        lock, so the probe costs one ffprobe per file per cache
        generation — never one per request.
        """
        # Use a sibling .tmp file with the same stem so ffmpeg knows the
        # container format.  Explicitly pass -f mp4 as a belt-and-suspenders
        # guard against Windows builds that can't infer format from .tmp.
        tmp_path = out_path.parent / (out_path.stem + "_tmp.mp4")

        ffmpeg = self._ffmpeg
        if ffmpeg is None:  # pragma: no cover - get_transcoded_path guards this
            raise TranscodeUnavailable(
                "ffmpeg not found next to the app or on PATH; "
                "cannot transcode video"
            )

        if self._can_stream_copy(source):
            try:
                self._run_ffmpeg(
                    self._copy_cmd(ffmpeg, source, tmp_path), source, tmp_path
                )
            except TranscodeError as exc:
                # e.g. an exotic stream ffmpeg refuses to put in MP4.  The
                # encode below is the pre-#853 path and still works.
                logger.warning(
                    "Stream copy failed for {} ({}) — re-encoding", source.name, exc
                )
            else:
                os.replace(tmp_path, out_path)
                logger.info(
                    "Remuxed (no re-encode) {} → {}", source.name, out_path.name
                )
                return

        self._run_ffmpeg(
            self._encode_cmd(ffmpeg, self._h264_encoder, source, tmp_path),
            source,
            tmp_path,
        )
        os.replace(tmp_path, out_path)
        logger.info("Transcoded {} → {}", source.name, out_path.name)

    @staticmethod
    def _copy_cmd(ffmpeg: str, source: Path, tmp_path: Path) -> list[str]:
        """ffmpeg argv that remuxes ``source`` into MP4 without re-encoding.

        ``-map`` is explicit so the output carries exactly the streams
        the probe vetted: the first video stream and, if present, the
        first audio stream.  Default stream selection could otherwise
        pull in a timecode/data track that MP4 refuses.
        """
        return [
            ffmpeg,
            "-i", str(source),
            "-map", "0:v:0",
            "-map", "0:a:0?",
            "-c", "copy",
            "-movflags", "+faststart",
            "-f", "mp4",
            "-y",
            str(tmp_path),
        ]

    @staticmethod
    def _encode_cmd(
        ffmpeg: str, encoder: str, source: Path, tmp_path: Path
    ) -> list[str]:
        """ffmpeg argv for the full H.264 re-encode.

        ``encoder`` comes from :func:`_select_h264_encoder` (#854).  The
        quality/speed options are encoder-specific — ``-preset``/``-crf``
        are libx264 options that libopenh264 rejects outright — so each
        encoder brings its own, and everything else stays the shared
        pre-#854 recipe.
        """
        if encoder == "libopenh264":
            # Cisco's encoder (the only software H.264 encoder in an LGPL
            # build) has no CRF mode: rate control is a target bitrate.
            # 4 Mbps is generous for the 1080p-and-below sources this
            # fallback serves and keeps the artifact a throwaway.
            quality_opts = ["-b:v", "4M"]
        else:
            # ultrafast (was "fast", #737): this is a throwaway H.264 stream the
            # browser plays once and the result is cached to disk, so encode
            # SPEED matters far more than output size — ultrafast is the fastest
            # libx264 preset and directly cuts the first-byte transcode stall
            # that every first view of the owner's ~99%-HEVC library pays. The
            # larger output is irrelevant (cached, then discarded). Progressive
            # streaming + a real progress % is the proper follow-up fix.
            quality_opts = ["-preset", "ultrafast", "-crf", "23"]

        return [
            ffmpeg,
            "-i", str(source),
            "-c:v", encoder,
            *quality_opts,
            # #853: without this the encoder inherits the SOURCE pixel
            # format, so a 10-bit or 4:2:2 input re-encoded to 10-bit/4:2:2
            # H.264 — which browsers refuse exactly as they refuse the
            # original.  Routing such a source here (instead of copying it)
            # is only worth anything if the encode normalises, so it does.
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-movflags", "+faststart",
            "-f", "mp4",
            "-y",
            str(tmp_path),
        ]

    def _run_ffmpeg(self, cmd: list[str], source: Path, tmp_path: Path) -> None:
        """Run one ffmpeg ``cmd`` that writes ``tmp_path``.

        Acquires ``_TRANSCODE_SEM`` only around the subprocess call so
        the concurrency cap covers only the CPU/IO-bound ffmpeg work,
        not the lock-wait or cache-check.  Raises ``TranscodeError`` on
        timeout, non-zero exit, or a missing output file, always after
        removing the partial staging file.
        """
        _TRANSCODE_SEM.acquire()
        try:
            result = subprocess.run(
                cmd,
                shell=False,
                timeout=300,
                check=False,
                capture_output=True,
                creationflags=_CREATE_NO_WINDOW,
            )
        except subprocess.TimeoutExpired as exc:
            # A hung/oversized encode must not leave a partial staging file
            # nor surface as a raw TimeoutExpired (route maps TranscodeError).
            self._remove_tmp(tmp_path)
            raise TranscodeError(
                f"ffmpeg timed out after 300s for {source!r}"
            ) from exc
        finally:
            _TRANSCODE_SEM.release()

        if result.returncode != 0:
            self._remove_tmp(tmp_path)
            stderr = result.stderr.decode("utf-8", errors="replace")
            raise TranscodeError(
                f"ffmpeg exited {result.returncode} for {source!r}: {stderr[:400]}"
            )

        if not tmp_path.exists():
            # ffmpeg reported success but wrote nothing — matches the
            # "output is missing" contract in get_transcoded_path's docstring.
            raise TranscodeError(f"ffmpeg produced no output for {source!r}")

    def _can_stream_copy(self, source: Path) -> bool:
        """Whether ``source`` can be remuxed into MP4 without re-encoding.

        True only when ffprobe reports that the first video stream is
        H.264 **in a browser-decodable pixel format**
        (:data:`_COPYABLE_PIX_FMTS` — 8-bit 4:2:0) and the FIRST audio
        stream, if there is one, is one of
        :data:`_COPYABLE_AUDIO_CODECS`.

        Only the first audio stream is judged (#862) because it is the
        only one the copy can produce: :meth:`_copy_cmd` maps
        ``0:v:0`` + ``0:a:0?``, so every later audio track is dropped
        from the output whichever recipe runs.  Vetoing on one used to
        send a camera/screen-recorder file with AAC first and a PCM
        commentary track second through the full libx264 encode, whose
        output is the same ``h264 + aac`` pair the copy would have
        produced — a multi-second stall bought for nothing.

        Never raises and never blocks a request: ffprobe missing, a
        non-zero exit, a timeout, unparseable output, or a file with no
        video stream all answer ``False``, which is exactly the pre-#853
        behaviour (full libx264 encode).  The failure is logged once —
        the caller only reaches here on a cache miss, and a successful
        encode then makes the next request a cache hit.

        The probe holds :data:`_TRANSCODE_SEM` while it runs (#862), so
        ffprobe and ffmpeg share one cap of 2 concurrent media
        sub-processes.  It is acquired and released around the probe
        alone, before :meth:`_run_ffmpeg` acquires it for the encode —
        sequentially, never nested, so the two cannot deadlock.
        """
        if self._ffprobe is None:
            return False

        cmd = [
            self._ffprobe,
            "-v", "error",
            "-print_format", "json",
            # Only the three fields the verdict needs; keeps the output
            # small and the parse trivial.
            "-show_entries", "stream=codec_type,codec_name,pix_fmt",
            str(source),
        ]
        # Same cap as the encode (#862).  Before #853 every media
        # sub-process this service spawned was bounded by _TRANSCODE_SEM;
        # adding an unbounded probe meant N cold requests for N distinct
        # files could hold N ffprobe processes for up to _PROBE_TIMEOUT_S
        # each, all reading the same (possibly NAS) disk the capped
        # ffmpeg runs are reading.  Sharing the existing semaphore
        # restores that invariant with one number instead of two.
        _TRANSCODE_SEM.acquire()
        try:
            result = subprocess.run(
                cmd,
                shell=False,
                timeout=_PROBE_TIMEOUT_S,
                check=False,
                capture_output=True,
                creationflags=_CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning(
                "ffprobe failed for {} ({}) — re-encoding", source.name, exc
            )
            return False
        finally:
            _TRANSCODE_SEM.release()

        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace")
            logger.warning(
                "ffprobe exited {} for {} ({}) — re-encoding",
                result.returncode, source.name, stderr[:200],
            )
            return False

        streams = _parse_probe_streams(result.stdout)
        if streams is None:
            logger.warning(
                "ffprobe output for {} was not parseable — re-encoding", source.name
            )
            return False

        video = [s for s in streams if s.get("codec_type") == "video"]
        if not video:
            # Audio-only or a container ffprobe could not make sense of:
            # unknown territory, so keep the encode.
            return False
        if video[0].get("codec_name") != _COPYABLE_VIDEO_CODEC:
            return False
        if video[0].get("pix_fmt") not in _COPYABLE_PIX_FMTS:
            # High 10 / 4:2:2 / 4:4:4 H.264, or an ffprobe build that did
            # not report pix_fmt at all.  Either way we cannot promise the
            # browser can decode the bitstream, so re-encode it (which
            # normalises to yuv420p — see _encode_cmd).
            return False

        audio = [s for s in streams if s.get("codec_type") == "audio"]
        if not audio:
            # A silent clip: `-map 0:a:0?` is optional, so the copy just
            # produces a video-only MP4.
            return True
        return audio[0].get("codec_name") in _COPYABLE_AUDIO_CODECS

    @staticmethod
    def _remove_tmp(tmp_path: Path) -> None:
        """Best-effort removal of a partial staging file (never raises)."""
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
