"""HTTP surface + label persistence for the visual ground-truth harness.

The bugs these encode: the server handing out bytes for a file that is not
in the sample (it would be a general file reader on the user's disk), a
label round-trip that loses or duplicates rows, and a resume that re-offers
a group the user already judged.
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import threading
import urllib.error
import urllib.request

from PIL import Image
import pytest

from scripts.visual_gt import server as srv


def _write_png(path: Path, size=(64, 48), colour=(200, 30, 30)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, colour).save(path, format="PNG")
    return path


def _strip_comments(lines) -> list[str]:
    """Drop the leading '#' precedence note so DictReader sees the header."""
    return [line for line in lines if not line.lstrip().startswith("#")]


def _read_rows(csv_path: Path) -> list[dict]:
    with csv_path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(_strip_comments(handle)))


def _group(key: str, paths, source: str = "burst") -> dict:
    return {
        "key": key,
        "source": source,
        "stratum": "burst_len_2",
        "summary": "test camera | 0.01s | no burst id",
        "members": [
            {"path": str(p), "name": Path(p).name, "shot_date": None, "file_size_bytes": 10}
            for p in paths
        ],
    }


@pytest.fixture
def session(tmp_path):
    photos = [_write_png(tmp_path / "lib" / f"{name}.png") for name in ("a", "b")]
    groups = [
        _group("burst:one", photos),
        _group("burst:two", [photos[0]]),
    ]
    return srv.LabelSession(groups, tmp_path / "gt.csv", tmp_path / "thumbs")


@pytest.fixture
def live(session):
    """A real server on an ephemeral loopback port."""
    httpd = srv.build_server(session, 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        yield base, session
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _get(url: str):
    with urllib.request.urlopen(url, timeout=30) as response:
        return response.status, response.headers, response.read()


def _post_json(url: str, payload: dict):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.status, json.loads(response.read())


class TestAllowlist:
    """The allowlist is built once at startup; group data alone must not widen it."""

    def test_a_member_path_outside_the_allowlist_is_not_served(self, session, tmp_path):
        outsider = _write_png(tmp_path / "secret" / "private.png")
        tampered = list(session.groups) + [_group("burst:evil", [outsider])]
        assert srv.resolve_member(tampered, session.allowed, 2, 0) is None

    def test_an_allowed_member_resolves(self, session):
        assert srv.resolve_member(session.groups, session.allowed, 0, 0) is not None

    def test_out_of_range_indices_resolve_to_nothing(self, session):
        assert srv.resolve_member(session.groups, session.allowed, 99, 0) is None
        assert srv.resolve_member(session.groups, session.allowed, 0, 99) is None

    def test_static_traversal_is_rejected(self):
        assert srv.safe_static_name("../../pyproject.toml") is None
        assert srv.safe_static_name("..\\..\\pyproject.toml") is None
        assert srv.safe_static_name("index.html") == "index.html"

    @pytest.mark.skipif(
        os.name != "nt",
        reason="drive-relative escape is Windows-only; on POSIX ':' is an ordinary "
        "filename character and the join stays inside the root. Skipping loses "
        "nothing on Linux/macOS because the vulnerable join cannot occur there.",
    )
    def test_a_drive_relative_name_cannot_escape_static(self, tmp_path, monkeypatch):
        """`C:desktop.ini` has no separator, yet discards the static root.

        pathlib only drops the left operand when the name carries a
        DIFFERENT drive, so the static root is put on an unused letter to
        make the escape deterministic rather than depending on which real
        drives this machine happens to have mapped. The false-positive
        half (a normal name still resolves) is asserted in
        ``test_static_traversal_is_rejected`` against the real static dir.
        """
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "desktop.ini").write_text("not yours", encoding="utf-8")
        decoy_drive = Path(outside).drive  # e.g. "C:"

        unused = next(
            letter
            for letter in "QZYXW"
            if f"{letter}:" != decoy_drive.upper() and not Path(f"{letter}:/").exists()
        )
        monkeypatch.setattr(srv, "STATIC_DIR", Path(f"{unused}:/static"))
        monkeypatch.chdir(outside)

        assert srv.safe_static_name(f"{decoy_drive}desktop.ini") is None


class TestHttp:
    def test_thumbnail_is_jpeg(self, live):
        base, _ = live
        status, headers, body = _get(f"{base}/thumb/0/0?size=1024")
        assert status == 200
        assert headers["Content-Type"] == "image/jpeg"
        assert body[:2] == b"\xff\xd8"

    def test_thumbnail_size_outside_the_allowlist_is_404(self, live):
        base, _ = live
        with pytest.raises(urllib.error.HTTPError) as caught:
            _get(f"{base}/thumb/0/0?size=99999")
        assert caught.value.code == 404

    def test_thumbnail_for_a_group_that_does_not_exist_is_404(self, live):
        base, _ = live
        with pytest.raises(urllib.error.HTTPError) as caught:
            _get(f"{base}/thumb/99/0?size=1024")
        assert caught.value.code == 404

    def test_group_payload_carries_thumb_and_zoom_urls(self, live):
        base, _ = live
        _, _, body = _get(f"{base}/api/group/0")
        payload = json.loads(body)
        assert payload["key"] == "burst:one"
        assert payload["members"][0]["thumb"] == "/thumb/0/0?size=1024"
        assert payload["members"][0]["zoom"] == "/thumb/0/0?size=2048"

    def test_index_page_is_served_at_root(self, live):
        base, _ = live
        status, headers, body = _get(f"{base}/")
        assert status == 200
        assert headers["Content-Type"].startswith("text/html")
        assert b"visual ground truth" in body

    def test_head_returns_the_headers_without_a_body(self, live):
        """curl -I probes with HEAD; BaseHTTPRequestHandler 501s without this."""
        base, _ = live
        request = urllib.request.Request(f"{base}/thumb/0/0?size=1024", method="HEAD")
        with urllib.request.urlopen(request, timeout=30) as response:
            assert response.status == 200
            assert response.headers["Content-Type"] == "image/jpeg"
            assert int(response.headers["Content-Length"]) > 0
            assert response.read() == b""

    def test_unknown_route_is_404(self, live):
        base, _ = live
        with pytest.raises(urllib.error.HTTPError) as caught:
            _get(f"{base}/api/nope")
        assert caught.value.code == 404


class TestLabelRoundTrip:
    def test_post_appends_one_row_per_member(self, live):
        base, session = live
        status, payload = _post_json(
            f"{base}/api/label",
            {
                "index": 0,
                "ranks": {session.groups[0]["members"][0]["path"]: 1},
                "excluded": [session.groups[0]["members"][1]["path"]],
                "case_tags": ["burst/action"],
                "confidence": "clear winner",
            },
        )
        assert status == 200 and payload["rows"] == 2

        rows = _read_rows(session.csv_path)
        assert [r["rank"] for r in rows] == ["1", ""]
        assert [r["excluded"] for r in rows] == ["", "1"]
        assert {r["group_key"] for r in rows} == {"burst:one"}
        assert rows[0]["case_tags"] == "burst/action"
        assert rows[0]["confidence"] == "clear winner"

    def test_a_labelled_group_is_skipped_on_restart(self, live, tmp_path):
        base, session = live
        _post_json(
            f"{base}/api/label",
            {
                "index": 0,
                "ranks": {session.groups[0]["members"][0]["path"]: 1},
                "confidence": "toss-up",
            },
        )
        restarted = srv.LabelSession(session.groups, session.csv_path, tmp_path / "thumbs2")
        state = restarted.state()
        assert state["labelled"] == 1
        assert state["next_index"] == 1

    def test_relabelling_needs_an_explicit_overwrite(self, live):
        """Skip on a labelled group used to silently replace the judgement."""
        base, session = live
        _post_json(
            f"{base}/api/label",
            {
                "index": 0,
                "ranks": {session.groups[0]["members"][0]["path"]: 1},
                "confidence": "clear winner",
            },
        )
        before = session.csv_path.read_bytes()

        with pytest.raises(urllib.error.HTTPError) as caught:
            _post_json(f"{base}/api/label", {"index": 0, "skipped": True})
        assert caught.value.code == 409
        assert session.csv_path.read_bytes() == before

        status, payload = _post_json(
            f"{base}/api/label", {"index": 0, "skipped": True, "overwrite": True}
        )
        assert status == 200 and payload["rows"] == 2
        assert session.csv_path.read_bytes() != before

    def test_two_row_sets_one_second_apart_stay_distinguishable(self, live):
        """A seconds-only stamp merged both submissions into one run."""
        base, session = live
        member = session.groups[0]["members"][0]["path"]
        _post_json(
            f"{base}/api/label",
            {"index": 0, "ranks": {member: 1}, "confidence": "clear winner"},
        )
        _post_json(
            f"{base}/api/label",
            {"index": 0, "ranks": {member: 1}, "confidence": "toss-up", "overwrite": True},
        )
        with session.csv_path.open(newline="", encoding="utf-8") as handle:
            rows = [r for r in csv.DictReader(_strip_comments(handle))]
        stamps = {row["labelled_at"] for row in rows}
        assert len(rows) == 4 and len(stamps) == 2
        assert session.state()["labelled"] == 1

    def test_an_invalid_payload_is_rejected_without_writing(self, live):
        base, session = live
        with pytest.raises(urllib.error.HTTPError) as caught:
            _post_json(f"{base}/api/label", {"index": 0, "ranks": {}, "confidence": "toss-up"})
        assert caught.value.code == 400
        assert not session.csv_path.exists()

    def test_skip_is_recorded_so_the_group_is_not_re_offered(self, live):
        base, session = live
        status, _ = _post_json(f"{base}/api/label", {"index": 0, "skipped": True})
        assert status == 200
        rows = _read_rows(session.csv_path)
        assert {r["confidence"] for r in rows} == {"skipped"}
        assert session.state()["next_index"] == 1


class TestPartialWriteResume:
    """A hard kill mid-append must not hide the group it was writing."""

    def _label_group_zero(self, base: str, session) -> None:
        _post_json(
            f"{base}/api/label",
            {
                "index": 0,
                "ranks": {session.groups[0]["members"][0]["path"]: 1},
                "confidence": "clear winner",
            },
        )

    def test_a_truncated_final_row_leaves_the_group_unlabelled(self, live, tmp_path):
        base, session = live
        self._label_group_zero(base, session)
        assert len(session.groups[0]["members"]) == 2

        # Simulate the kill: the last row is cut off mid-field. Its
        # group_key still parses, which is exactly why presence-counting
        # marked the group done and resume hid it.
        data = session.csv_path.read_bytes()
        truncated = data[: data.rfind(b"\r\n", 0, len(data) - 2) + 2] + b"burst:one,burst,,clear"
        session.csv_path.write_bytes(truncated)

        restarted = srv.LabelSession(session.groups, session.csv_path, tmp_path / "t2")
        assert "burst:one" not in restarted.labelled
        assert restarted.state()["next_index"] == 0

    def test_a_complete_row_set_still_counts_as_labelled(self, live, tmp_path):
        """The false-positive half: an intact write must NOT be re-presented."""
        base, session = live
        self._label_group_zero(base, session)
        restarted = srv.LabelSession(session.groups, session.csv_path, tmp_path / "t3")
        assert "burst:one" in restarted.labelled
        assert restarted.state()["next_index"] == 1


class TestValidation:
    def test_missing_confidence_is_rejected(self):
        group = _group("k", ["a.png", "b.png"])
        assert "confidence" in srv.validate_label(group, {"ranks": {"a.png": 1}})

    def test_excluding_everything_needs_no_rank(self):
        group = _group("k", ["a.png", "b.png"])
        payload = {"confidence": "all bad", "excluded": ["a.png", "b.png"], "ranks": {}}
        assert srv.validate_label(group, payload) is None

    def test_a_path_outside_the_group_is_rejected(self):
        group = _group("k", ["a.png"])
        payload = {"confidence": "toss-up", "ranks": {"elsewhere.png": 1}}
        assert "outside the group" in srv.validate_label(group, payload)

    def test_a_path_cannot_be_both_ranked_and_excluded(self):
        group = _group("k", ["a.png", "b.png"])
        payload = {"confidence": "toss-up", "ranks": {"a.png": 1}, "excluded": ["a.png"]}
        assert "both ranked and excluded" in srv.validate_label(group, payload)

    def test_an_unknown_case_tag_is_rejected(self):
        group = _group("k", ["a.png"])
        payload = {"confidence": "toss-up", "ranks": {"a.png": 1}, "case_tags": ["nope"]}
        assert "unknown case tag" in srv.validate_label(group, payload)

    def test_a_skip_needs_nothing_else(self):
        assert srv.validate_label(_group("k", ["a.png"]), {"skipped": True}) is None


class TestThumbnailRendering:
    def test_orientation_tag_is_applied(self, tmp_path):
        """A 6 (rotate 90 CW) orientation must come back transposed."""
        source = tmp_path / "rot.jpg"
        image = Image.new("RGB", (80, 40), (10, 200, 10))
        exif = image.getexif()
        exif[274] = 6
        image.save(source, format="JPEG", exif=exif)

        rendered = srv.load_source_image(source, 1024)
        assert rendered.size == (40, 80)

    def test_an_undecodable_file_returns_none_rather_than_raising(self, tmp_path):
        broken = tmp_path / "broken.jpg"
        broken.write_bytes(b"not an image")
        assert srv.load_source_image(broken, 1024) is None
        assert srv.render_thumbnail(broken, 1024) is None

    def test_concurrent_renders_of_one_thumb_all_succeed(self, tmp_path):
        """Prefetch thread and browser request hit the same entry at once.

        With a shared `<digest>_<size>.part` name the writers collided and
        Windows raised PermissionError (WinError 32) in the request thread,
        which had no handler, so the client saw a dropped connection.
        """
        source = _write_png(tmp_path / "lib" / "race.png", size=(900, 700))
        thumbs = tmp_path / "thumbs"
        thumbs.mkdir()
        workers = 16
        barrier = threading.Barrier(workers)
        results: list[bytes] = []
        errors: list[BaseException] = []
        lock = threading.Lock()

        def render_once() -> None:
            try:
                barrier.wait(timeout=30)
                data = srv.cached_thumbnail(thumbs, str(source), 1024)
                with lock:
                    results.append(data)
            except BaseException as exc:  # noqa: BLE001 - the test is the assertion
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=render_once) for _ in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        assert errors == []
        assert len(results) == workers
        assert all(data and data[:2] == b"\xff\xd8" for data in results)
        assert srv.thumb_cache_path(thumbs, str(source), 1024).exists()
        assert list(thumbs.glob("*.part")) == []

    def test_a_second_request_is_served_from_the_cache_file(self, tmp_path):
        source = _write_png(tmp_path / "lib" / "c.png")
        thumbs = tmp_path / "thumbs"
        thumbs.mkdir()
        first = srv.cached_thumbnail(thumbs, str(source), 1024)
        cache = srv.thumb_cache_path(thumbs, str(source), 1024)
        assert cache.exists()
        cache.write_bytes(b"\xff\xd8sentinel")
        assert srv.cached_thumbnail(thumbs, str(source), 1024) == b"\xff\xd8sentinel"
        assert first[:2] == b"\xff\xd8"


class TestThumbDirDefault:
    def test_cache_dir_shares_the_csv_stem_so_one_ignore_rule_covers_it(self, tmp_path):
        """A bare 'thumbs/' escaped `qa/fixtures/visual-gt*` and exposed photos."""
        csv_path = tmp_path / "qa" / "fixtures" / "visual-gt.csv"
        cache = srv.default_thumb_dir(csv_path)
        assert cache.name == "visual-gt-thumbs"
        assert cache.parent == csv_path.parent


class TestLoadGroups:
    def test_sidecar_without_groups_yields_an_empty_list(self, tmp_path):
        path = tmp_path / "groups.json"
        path.write_text(json.dumps({"schema": 1}), encoding="utf-8")
        assert srv.load_groups(path) == []
