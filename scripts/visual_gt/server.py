"""Local labelling server for the visual ground-truth study.

Serves the sample written by :mod:`scripts.visual_gt.bootstrap` on
127.0.0.1 and appends hand-labels to a CSV. Stdlib ``http.server`` only —
no framework, no CDN, no new dependency.

Safety properties this module is responsible for:

* It binds to ``127.0.0.1`` and nothing else.
* It serves image bytes ONLY for paths that appear in the bootstrapped
  sample (:func:`resolve_member` checks an exact-path allowlist built at
  startup, so a group file that was edited after the fact still cannot
  turn the server into a general file reader).
* It never writes to, moves, or deletes a photo. The only files it
  creates are JPEG thumbnails under ``<out_dir>/thumbs/`` and the label
  CSV.

The CSV is an append-only log and the **last complete row-set per
``group_key`` wins**. A row-set is complete when it holds exactly one row
per group member; a partial one (a hard kill mid-append) is ignored, so
resume re-presents that group rather than hiding it. Re-labelling an
already-labelled group is refused with 409 unless the request carries
``overwrite: true``.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import secrets
import threading
from typing import Optional, Sequence
from urllib.parse import parse_qs, urlparse

from PIL import Image, ImageOps

try:  # pillow-heif is a hard dependency of the app; guard anyway.
    from pillow_heif import register_heif_opener

    register_heif_opener()
except Exception:  # pragma: no cover - environment without pillow-heif
    pass

CSV_HEADER = (
    "group_key",
    "source",
    "case_tags",
    "confidence",
    "path",
    "rank",
    "excluded",
    "labelled_at",
)

# Written above the header on file creation. Readers skip leading "#"
# lines; this states the precedence rule for the human who opens the CSV.
CSV_PRECEDENCE_NOTE = (
    "# visual-gt labels. Append-only log: the LAST COMPLETE row-set per "
    "group_key wins. A row-set is complete when it has one row per group "
    "member; a partial one is ignored and the group is re-presented.\r\n"
)

CASE_TAGS = (
    "burst/action",
    "group portrait",
    "long exposure",
    "bracketed",
    "near-identical edits",
    "other",
)
CONFIDENCE_CHOICES = ("clear winner", "toss-up", "all bad")
THUMB_SIZES = (1024, 2048)
RAW_EXTENSIONS = frozenset({".dng", ".cr2", ".cr3", ".nef", ".arw", ".raf", ".rw2"})
STATIC_DIR = Path(__file__).resolve().parent / "static"


class LabelSession:
    """Mutable server state: the sample, the allowlist, and the CSV."""

    def __init__(self, groups: Sequence[dict], csv_path: Path, thumb_dir: Path) -> None:
        self.groups = list(groups)
        self.allowed = frozenset(
            member["path"] for group in self.groups for member in group["members"]
        )
        self.csv_path = csv_path
        self.thumb_dir = thumb_dir
        self.thumb_dir.mkdir(parents=True, exist_ok=True)
        self.labelled: set[str] = self._complete_keys()
        self.write_lock = threading.Lock()
        self._prefetching: set[str] = set()
        self._prefetch_lock = threading.Lock()

    def _complete_keys(self) -> set[str]:
        """Keys whose LAST row-set has one row per member.

        A hard kill part-way through an append leaves a short final
        row-set whose ``group_key`` still parses. Counting presence would
        mark that group done and resume would hide it permanently, so the
        row count is compared against the group's member count instead.
        """
        from scripts.visual_gt.bootstrap import read_last_rowset_sizes  # noqa: PLC0415

        sizes = read_last_rowset_sizes(self.csv_path)
        return {
            group["key"]
            for group in self.groups
            if sizes.get(group["key"]) == len(group["members"])
        }

    def claim_prefetch(self, key: str) -> bool:
        """True the first time a group key is claimed — one warm-up per group."""
        with self._prefetch_lock:
            if key in self._prefetching:
                return False
            self._prefetching.add(key)
            return True

    def state(self) -> dict:
        keys = [group["key"] for group in self.groups]
        next_index = next(
            (i for i, key in enumerate(keys) if key not in self.labelled), len(keys)
        )
        return {
            "total": len(keys),
            "labelled": sum(1 for key in keys if key in self.labelled),
            "next_index": next_index,
            "labelled_keys": sorted(self.labelled),
            "case_tags": list(CASE_TAGS),
            "confidence_choices": list(CONFIDENCE_CHOICES),
            "csv_path": str(self.csv_path),
        }


# --- Pure helpers (unit-tested) ----------------------------------------


def resolve_member(
    groups: Sequence[dict], allowed: frozenset[str], index: int, member_index: int
) -> Optional[str]:
    """Path for ``groups[index]['members'][member_index]``, or None.

    None means "404": the index is out of range, or the path is not in the
    startup allowlist. The allowlist check is the one that matters — it is
    what keeps this from being a general-purpose file reader.
    """
    if not 0 <= index < len(groups):
        return None
    members = groups[index]["members"]
    if not 0 <= member_index < len(members):
        return None
    path = members[member_index]["path"]
    return path if path in allowed else None


def safe_static_name(name: str) -> Optional[str]:
    """Reject anything that is not a plain file inside ``static/``.

    The separator check alone does not confine on Windows: ``J:desktop.ini``
    contains neither ``/`` nor ``\\``, but it carries a drive letter, so
    ``STATIC_DIR / name`` discards STATIC_DIR entirely and resolves against
    J:'s current directory. Confinement is therefore decided by resolving
    the candidate and requiring it to sit under the resolved static root.
    """
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return None
    root = STATIC_DIR.resolve()
    try:
        candidate = (root / name).resolve()
    except OSError:
        return None
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return name


def validate_label(group: dict, payload: dict) -> Optional[str]:
    """Return an error message, or None when the payload is acceptable."""
    if payload.get("skipped"):
        return None
    confidence = payload.get("confidence")
    if confidence not in CONFIDENCE_CHOICES:
        return f"confidence must be one of {CONFIDENCE_CHOICES}"
    paths = {member["path"] for member in group["members"]}
    excluded = set(payload.get("excluded") or [])
    if not excluded <= paths:
        return "excluded contains a path outside the group"
    ranks = payload.get("ranks") or {}
    if not set(ranks) <= paths:
        return "ranks contains a path outside the group"
    if set(ranks) & excluded:
        return "a path cannot be both ranked and excluded"
    for tag in payload.get("case_tags") or []:
        if tag not in CASE_TAGS:
            return f"unknown case tag {tag!r}"
    if not ranks and excluded != paths:
        return "rank at least one photo, or exclude all of them"
    return None


def build_label_rows(group: dict, payload: dict, labelled_at: str) -> list[list[str]]:
    """One CSV row per member, in the group's display order."""
    skipped = bool(payload.get("skipped"))
    confidence = "skipped" if skipped else str(payload.get("confidence") or "")
    tags = "" if skipped else "|".join(payload.get("case_tags") or [])
    ranks = {} if skipped else (payload.get("ranks") or {})
    excluded = set() if skipped else set(payload.get("excluded") or [])
    rows = []
    for member in group["members"]:
        path = member["path"]
        rank = ranks.get(path)
        rows.append(
            [
                group["key"],
                group.get("source", ""),
                tags,
                confidence,
                path,
                "" if rank in (None, "") else str(int(rank)),
                "1" if path in excluded else "",
                labelled_at,
            ]
        )
    return rows


def append_label_rows(csv_path: Path, rows: Sequence[Sequence[str]]) -> None:
    """Append rows and fsync once — one durable write per labelled group."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    need_header = not csv_path.exists() or csv_path.stat().st_size == 0
    with csv_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if need_header:
            handle.write(CSV_PRECEDENCE_NOTE)
            writer.writerow(CSV_HEADER)
        writer.writerows(rows)
        handle.flush()
        os.fsync(handle.fileno())


# --- Thumbnails ---------------------------------------------------------


def thumb_cache_path(thumb_dir: Path, source: str, size: int) -> Path:
    digest = hashlib.sha1(source.encode("utf-8")).hexdigest()[:16]
    return thumb_dir / f"{digest}_{size}.jpg"


def _load_raw(path: Path) -> Optional[Image.Image]:
    """Embedded preview for a RAW file, via the scanner's own helper."""
    try:
        from scanner.hasher import _load_raw_preview  # noqa: PLC0415
    except Exception:  # pragma: no cover - scanner import failure
        return None
    return _load_raw_preview(path)


def load_source_image(path: Path, size: int) -> Optional[Image.Image]:
    """Decode ``path`` to an oriented RGB image, or None if it cannot be read.

    JPEG goes through ``draft()`` (libjpeg's DCT scaler) so a 48 MP file is
    not fully decoded just to make a 1024 px thumbnail. RAW uses the
    embedded preview the scanner already knows how to pull.
    """
    if path.suffix.lower() in RAW_EXTENSIONS:
        image = _load_raw(path)
        return None if image is None else ImageOps.exif_transpose(image)
    try:
        with Image.open(path) as source:
            source.draft("RGB", (size, size))
            oriented = ImageOps.exif_transpose(source)
            image = oriented.convert("RGB")
            image.load()
            return image
    except (OSError, ValueError):
        return None


def render_thumbnail(path: Path, size: int) -> Optional[bytes]:
    """JPEG bytes for ``path`` at most ``size`` px on its long edge."""
    image = load_source_image(path, size)
    if image is None:
        return None
    # Image.Resampling.LANCZOS, not the bare Image.LANCZOS alias: both are 1
    # at runtime, but only the enum member is in Pillow 12's type stubs.
    image.thumbnail((size, size), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=88, optimize=True)
    return buffer.getvalue()


def cached_thumbnail(thumb_dir: Path, source: str, size: int) -> Optional[bytes]:
    """Thumbnail bytes, rendering and caching on first request.

    Concurrency matters here: the prefetch thread warming group N and the
    browser's own request for group N reach this for the same entry at the
    same moment. A shared ``<digest>_<size>.part`` name made those writers
    collide — on Windows, writing or replacing a file another thread holds
    open raises ``PermissionError`` (WinError 32), which the request thread
    had no handler for, so the client saw a dropped connection and the tile
    rendered "decode failed". Each writer therefore gets its own temp name,
    and losing the replace race is success: the winner's bytes are for the
    same source at the same size, so they are equivalent.
    """
    cache = thumb_cache_path(thumb_dir, source, size)
    try:
        if cache.exists():
            return cache.read_bytes()
    except OSError:
        # A concurrent replace can briefly make the entry unreadable; fall
        # through and render rather than failing the request.
        pass
    data = render_thumbnail(Path(source), size)
    if data is None:
        return None
    unique = f"{os.getpid()}.{threading.get_ident()}.{secrets.token_hex(4)}"
    tmp = cache.with_name(f"{cache.name}.{unique}.part")
    tmp.write_bytes(data)
    try:
        os.replace(tmp, cache)
    except OSError:
        tmp.unlink(missing_ok=True)
        if not cache.exists():
            raise
        try:
            return cache.read_bytes()
        except OSError:
            return data
    return data


# --- HTTP ---------------------------------------------------------------


class LabelHandler(BaseHTTPRequestHandler):
    """Routes: ``/``, ``/static/*``, ``/api/*``, ``/thumb/<i>/<j>``."""

    protocol_version = "HTTP/1.1"
    server_version = "visual-gt/1.0"

    @property
    def session(self) -> LabelSession:
        return self.server.session  # type: ignore[attr-defined]

    # Parameter is named ``format`` (shadowing the builtin) to match
    # BaseHTTPRequestHandler.log_message exactly — the base class calls it
    # positionally today, but a renamed parameter is an incompatible
    # override and any keyword call from stdlib would break.
    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
        print(f"  {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}")

    # -- response helpers

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload).encode("utf-8"), "application/json")

    def _not_found(self) -> None:
        self._json(404, {"error": "not found"})

    # -- GET

    def do_GET(self) -> None:  # noqa: N802 - stdlib name
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        if not parts:
            return self._serve_static("index.html")
        if parts[0] == "static" and len(parts) == 2:
            return self._serve_static(parts[1])
        if parts[:2] == ["api", "state"]:
            return self._json(200, self.session.state())
        if parts[:2] == ["api", "group"] and len(parts) == 3:
            return self._serve_group(parts[2])
        if parts[0] == "thumb" and len(parts) == 3:
            return self._serve_thumb(parts[1], parts[2], parse_qs(parsed.query))
        return self._not_found()

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib name
        """Same routing as GET; :meth:`_send` drops the body for HEAD."""
        self.do_GET()

    def _serve_static(self, name: str) -> None:
        safe = safe_static_name(name)
        if safe is None:
            return self._not_found()
        kind = "text/html; charset=utf-8" if safe.endswith(".html") else "text/plain"
        self._send(200, (STATIC_DIR / safe).read_bytes(), kind)

    def _serve_group(self, raw_index: str) -> None:
        try:
            index = int(raw_index)
        except ValueError:
            return self._not_found()
        if not 0 <= index < len(self.session.groups):
            return self._not_found()
        group = self.session.groups[index]
        members = [
            {
                **member,
                "thumb": f"/thumb/{index}/{j}?size={THUMB_SIZES[0]}",
                "zoom": f"/thumb/{index}/{j}?size={THUMB_SIZES[1]}",
            }
            for j, member in enumerate(group["members"])
        ]
        self._start_prefetch(index + 1)
        self._json(
            200,
            {
                "index": index,
                "key": group["key"],
                "source": group.get("source", ""),
                "stratum": group.get("stratum", ""),
                "summary": group.get("summary", ""),
                "labelled": group["key"] in self.session.labelled,
                "members": members,
            },
        )

    def _serve_thumb(self, raw_index: str, raw_member: str, query: dict) -> None:
        try:
            index, member_index = int(raw_index), int(raw_member)
            size = int((query.get("size") or [THUMB_SIZES[0]])[0])
        except ValueError:
            return self._not_found()
        if size not in THUMB_SIZES:
            return self._not_found()
        source = resolve_member(self.session.groups, self.session.allowed, index, member_index)
        if source is None:
            return self._not_found()
        data = cached_thumbnail(self.session.thumb_dir, source, size)
        if data is None:
            return self._json(415, {"error": "could not decode", "path": Path(source).name})
        self._send(200, data, "image/jpeg")

    def _start_prefetch(self, index: int) -> None:
        """Warm the next group's small thumbs on a daemon thread."""
        session = self.session
        if not 0 <= index < len(session.groups):
            return
        if not session.claim_prefetch(session.groups[index]["key"]):
            return

        def run() -> None:
            for j in range(len(session.groups[index]["members"])):
                source = resolve_member(session.groups, session.allowed, index, j)
                if source:
                    try:
                        cached_thumbnail(session.thumb_dir, source, THUMB_SIZES[0])
                    except Exception:  # pylint: disable=broad-exception-caught
                        pass

        threading.Thread(target=run, daemon=True).start()

    # -- POST

    def do_POST(self) -> None:  # noqa: N802 - stdlib name
        if urlparse(self.path).path.rstrip("/") != "/api/label":
            return self._not_found()
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._json(400, {"error": "bad JSON body"})
        index = payload.get("index")
        if not isinstance(index, int) or not 0 <= index < len(self.session.groups):
            return self._json(400, {"error": "index out of range"})
        group = self.session.groups[index]
        if group["key"] in self.session.labelled and not payload.get("overwrite"):
            # Without this, Skip on an already-labelled group (the only
            # one-key way forward before the UI had a Forward control) wrote
            # a full skipped row-set over a real judgement.
            return self._json(
                409,
                {
                    "error": "group already labelled; resend with overwrite: true to replace",
                    "key": group["key"],
                },
            )
        error = validate_label(group, payload)
        if error:
            return self._json(400, {"error": error})
        # Microseconds, not seconds: two row-sets written inside one second
        # would share a stamp and merge into a single run, which the
        # completeness check in LabelSession._complete_keys would then read
        # as the wrong size.
        rows = build_label_rows(group, payload, dt.datetime.now().isoformat(timespec="microseconds"))
        with self.session.write_lock:
            append_label_rows(self.session.csv_path, rows)
            self.session.labelled.add(group["key"])
        self._json(200, {"ok": True, "rows": len(rows), **self.session.state()})


def default_thumb_dir(csv_path: Path) -> Path:
    """Cache directory that shares the CSV's stem, so one ignore rule covers both."""
    return csv_path.parent / f"{csv_path.stem}-thumbs"


def load_groups(groups_path: Path) -> list[dict]:
    document = json.loads(groups_path.read_text(encoding="utf-8"))
    return list(document.get("groups") or [])


def build_server(session: LabelSession, port: int) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), LabelHandler)
    httpd.session = session  # type: ignore[attr-defined]
    return httpd


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.visual_gt.server",
        description="Serve the bootstrapped sample for hand-labelling on 127.0.0.1.",
    )
    parser.add_argument("--groups", required=True, help="JSON sidecar from bootstrap.py")
    parser.add_argument("--csv", required=True, help="label CSV (appended to; resumable)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--thumbs",
        default=None,
        help="thumbnail cache dir (default: <csv path minus extension>-thumbs/)",
    )
    args = parser.parse_args(argv)

    groups_path = Path(args.groups)
    csv_path = Path(args.csv)
    # Default sits beside the CSV and shares its stem, so ONE .gitignore
    # pattern (``qa/fixtures/visual-gt*``) covers the CSV, the JSON sidecar
    # and the cache. A separate ``thumbs/`` directory escaped that pattern
    # and would have offered the user's photos to `git add`.
    thumb_dir = Path(args.thumbs) if args.thumbs else default_thumb_dir(csv_path)
    session = LabelSession(load_groups(groups_path), csv_path, thumb_dir)
    if not session.groups:
        print(f"ERROR: {groups_path} contains no groups.")
        return 2

    httpd = build_server(session, args.port)
    state = session.state()
    print(f"groups: {state['total']}  already labelled: {state['labelled']}")
    print(f"csv:    {csv_path}")
    print(f"thumbs: {thumb_dir}")
    print(f"\n  open  http://127.0.0.1:{args.port}/\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
