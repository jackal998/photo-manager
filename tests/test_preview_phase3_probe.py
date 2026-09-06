"""Tests for the #622 Phase 3 preview instrument.

Scope: ``scripts/preview_phase3_analysis.py`` — the probe's PURE half, whose
silent breakage would put a wrong number into a citation. Its sibling
``scripts/preview_phase3_probe.py`` owns the Qt harness (``run_session`` /
``run_modal_check``), which is layer-3/manual by nature: it needs a
QApplication, a real image tree and a real decoder, and is exercised by
running the probe (see ``docs/audits/preview-phase3-runbook.md``), not from
the unit layer. The two are separate modules precisely so this line is a file
boundary rather than a convention.

Each test below protects one claim the artifact makes:

* the box-2 ratio is DERIVED from the two runs, and is never emitted for a
  pairing that did not actually compare the two decode routes;
* the box-1 verdict tracks the threshold in both directions;
* the artifact carries the four citation fields, and a payload missing one is
  reported rather than written silently;
* the JSON lands with LF endings on Windows (a CRLF rewrite would change every
  byte of an artifact meant to be diffed and hashed).
"""
from __future__ import annotations

import json

import pytest

import scripts.preview_phase3_analysis as probe


def _click(
    path: str,
    *,
    ttfp_ms: float = 100.0,
    decode_path: str = probe.PATH_EMBEDDED,
    ok: bool = True,
    source_loaded: bool = True,
    rss_bytes: int = 100_000_000,
    timed_out: bool = False,
) -> dict:
    """One per-click row, shaped exactly as ``run_session`` records it."""
    return {
        "path": path,
        "ttfp_ms": ttfp_ms,
        "decode_path": decode_path,
        "ok": ok,
        "source_loaded": source_loaded,
        "rss_bytes": rss_bytes,
        "timed_out": timed_out,
        "ext": ".dng",
        "lru_thumb_bytes": 1_000,
        "lru_preview_bytes": 2_000,
    }


class TestDiscoverImages:
    def test_recurses_filters_by_extension_and_sorts(self, tmp_path):
        (tmp_path / "sub").mkdir()
        (tmp_path / "b.JPG").write_bytes(b"x")
        (tmp_path / "sub" / "a.dng").write_bytes(b"x")
        (tmp_path / "notes.txt").write_bytes(b"x")
        (tmp_path / "clip.mp4").write_bytes(b"x")

        found = probe.discover_images(tmp_path, [".jpg", ".dng"])

        # .txt and .mp4 excluded; extension match is case-insensitive.
        # Order is by lowercased full path, so "<root>/b.jpg" precedes
        # "<root>/sub/a.dng" — the tree's own order, not the walk's.
        assert [p.name for p in found] == ["b.JPG", "a.dng"]
        # Deterministic: the box-2 pairing joins two runs by path, so a
        # non-stable order would silently shrink the pairing.
        assert probe.discover_images(tmp_path, [".jpg", ".dng"]) == found

    def test_accepts_extensions_without_a_leading_dot(self, tmp_path):
        (tmp_path / "a.jpg").write_bytes(b"x")
        assert len(probe.discover_images(tmp_path, ["jpg"])) == 1

    def test_empty_tree_returns_empty(self, tmp_path):
        assert probe.discover_images(tmp_path, [".jpg"]) == []


class TestClickOrder:
    def test_cycles_when_the_library_is_smaller_than_the_budget(self, tmp_path):
        files = [tmp_path / "a.jpg", tmp_path / "b.jpg"]
        assert probe.click_order(files, 5) == [
            files[0], files[1], files[0], files[1], files[0]
        ]

    def test_truncates_when_the_library_is_larger(self, tmp_path):
        files = [tmp_path / f"{i}.jpg" for i in range(10)]
        assert probe.click_order(files, 3) == files[:3]

    @pytest.mark.parametrize("files,clicks", [([], 5), (["a"], 0), ([], 0)])
    def test_degenerate_inputs_yield_no_clicks(self, files, clicks):
        assert probe.click_order(list(files), clicks) == []


class TestSteadyWindow:
    def test_takes_the_tail(self):
        rows = [_click(f"p{i}") for i in range(10)]
        assert probe.steady_window(rows, 3) == rows[-3:]

    def test_window_wider_than_the_run_returns_the_whole_run(self):
        rows = [_click(f"p{i}") for i in range(4)]
        # A 20-click smoke run must still produce a box-1 reading; the summary
        # records steady_window_used so the under-sizing stays visible.
        assert probe.steady_window(rows, 50) == rows


class TestFirstSourceLoadByPath:
    def test_keeps_the_first_cold_decode_and_ignores_cache_hits(self):
        rows = [
            _click("a", ttfp_ms=900.0),
            _click("a", ttfp_ms=5.0, decode_path=probe.PATH_CACHE_HIT,
                   source_loaded=False),
            _click("b", ttfp_ms=800.0),
        ]
        picked = probe.first_source_load_by_path(rows)
        assert picked["a"]["ttfp_ms"] == 900.0  # not the 5 ms cache hit
        assert set(picked) == {"a", "b"}

    def test_timed_out_clicks_are_not_a_measurement(self):
        rows = [_click("a", ok=False, timed_out=True, ttfp_ms=None)]
        assert probe.first_source_load_by_path(rows) == {}


class TestComputeBox2Ratio:
    def test_ratio_is_derived_from_the_two_runs(self):
        embedded = [_click("a", ttfp_ms=100.0), _click("b", ttfp_ms=200.0)]
        forced = [
            _click("a", ttfp_ms=800.0, decode_path=probe.PATH_FORCED_FULL),
            _click("b", ttfp_ms=1200.0, decode_path=probe.PATH_FORCED_FULL),
        ]
        out = probe.compute_box2_ratio(embedded, forced, threshold=5.0)

        assert out["status"] == "measured"
        assert out["n_paths"] == 2
        # per-path ratios are 8.0 and 6.0 -> median 7.0. Exact, so a change in
        # the maths cannot pass by landing "somewhere plausible".
        assert out["ratio_median"] == 7.0
        assert out["ratio_min"] == 6.0
        assert out["ratio_max"] == 8.0
        assert out["pass"] is True

    def test_below_threshold_fails(self):
        embedded = [_click("a", ttfp_ms=100.0)]
        forced = [_click("a", ttfp_ms=300.0, decode_path=probe.PATH_FORCED_FULL)]
        out = probe.compute_box2_ratio(embedded, forced, threshold=5.0)
        assert out["ratio_median"] == 3.0
        assert out["pass"] is False

    def test_excludes_paths_whose_baseline_did_not_take_the_embedded_route(self):
        # A JPEG (non_raw_source) compared against a forced decode is not a
        # comparison of the two DNG routes — including it would inflate the
        # ratio with a file the box says nothing about.
        embedded = [
            _click("jpeg", ttfp_ms=10.0, decode_path=probe.PATH_NON_RAW),
            _click("dng", ttfp_ms=100.0),
        ]
        forced = [
            _click("jpeg", ttfp_ms=10.0, decode_path=probe.PATH_FORCED_FULL),
            _click("dng", ttfp_ms=1000.0, decode_path=probe.PATH_FORCED_FULL),
        ]
        out = probe.compute_box2_ratio(embedded, forced)
        assert out["n_paths"] == 1
        assert [p["path"] for p in out["pairs"]] == ["dng"]

    def test_excludes_a_comparison_run_that_did_not_force_the_full_decode(self):
        embedded = [_click("a", ttfp_ms=100.0)]
        forced = [_click("a", ttfp_ms=900.0)]  # still on the embedded route
        assert probe.compute_box2_ratio(embedded, forced)["status"] == "not_measured"

    def test_no_pairs_reports_not_measured_with_a_reason(self):
        # The honest result on a library with no DNG — what the local smoke
        # run produces. It must never read as a pass.
        out = probe.compute_box2_ratio([_click("a", decode_path=probe.PATH_NON_RAW)], [])
        assert out["status"] == "not_measured"
        assert out["pass"] is None
        assert out["reason"]
        assert out["n_paths"] == 0

    def test_zero_latency_rows_cannot_divide_by_zero(self):
        embedded = [_click("a", ttfp_ms=0.0)]
        forced = [_click("a", ttfp_ms=900.0, decode_path=probe.PATH_FORCED_FULL)]
        assert probe.compute_box2_ratio(embedded, forced)["status"] == "not_measured"


class TestSummariseClicks:
    def test_box1_passes_under_the_threshold_and_names_the_window(self):
        rows = [_click(f"p{i}", rss_bytes=300_000_000) for i in range(60)]
        summary = probe.summarise_clicks(rows, steady_window_n=50,
                                         rss_threshold_mb=600.0)
        box1 = summary["box1"]
        assert box1["steady_window_used"] == 50
        assert box1["steady_state_rss_mb"]["p50"] == 300.0
        assert box1["pass"] is True

    def test_box1_fails_over_the_threshold(self):
        # The other half of the value domain: a guard that only ever passes is
        # indistinguishable from one that is not looking.
        rows = [_click(f"p{i}", rss_bytes=700_000_000) for i in range(60)]
        box1 = probe.summarise_clicks(rows, steady_window_n=50)["box1"]
        assert box1["pass"] is False
        assert box1["pass_on_max"] is False

    def test_steady_state_ignores_the_warm_up_clicks(self):
        # 10 cheap clicks then 50 expensive ones: the tail is what box 1 means.
        rows = [_click(f"w{i}", rss_bytes=50_000_000) for i in range(10)]
        rows += [_click(f"s{i}", rss_bytes=700_000_000) for i in range(50)]
        box1 = probe.summarise_clicks(rows, steady_window_n=50)["box1"]
        assert box1["steady_state_rss_mb"]["p50"] == 700.0
        assert box1["pass"] is False

    def test_counts_timeouts_and_decode_paths(self):
        rows = [
            _click("a"),
            _click("b", decode_path=probe.PATH_CACHE_HIT, source_loaded=False),
            _click("c", ok=False, timed_out=True, ttfp_ms=None),
        ]
        summary = probe.summarise_clicks(rows, steady_window_n=3)
        assert summary["clicks"] == 3
        assert summary["ok_clicks"] == 2
        assert summary["timeouts"] == 1
        assert summary["decode_path_counts"][probe.PATH_CACHE_HIT] == 1

    def test_box2_defaults_to_not_measured_on_a_single_run(self):
        summary = probe.summarise_clicks([_click("a")], steady_window_n=1)
        assert summary["box2"]["status"] == "not_measured"
        assert summary["box2"]["pass"] is None

    def test_empty_run_reports_no_verdict_rather_than_a_pass(self):
        box1 = probe.summarise_clicks([], steady_window_n=50)["box1"]
        assert box1["pass"] is None


def _payload() -> dict:
    return probe.build_payload(
        args={"root": "R", "clicks": 2},
        argv=["preview_phase3_probe.py", "--root", "R"],
        git_sha="a" * 40,
        git_dirty=False,
        host={"hostname": "h"},
        timestamps={"started_utc": "t"},
        env={"root": "R"},
        per_click=[_click("a")],
        modal={"attempted": False},
        summary=probe.summarise_clicks([_click("a")], steady_window_n=1),
    )


class TestPayload:
    def test_carries_the_four_citation_fields(self):
        payload = _payload()
        # probe + SHA + args + (the JSON file itself) is the project's
        # perf-claim rule; a payload missing one cannot be cited.
        assert payload["probe"] == probe.PROBE_NAME
        assert payload["git_sha"] == "a" * 40
        assert payload["args"]["root"] == "R"
        assert payload["argv"][0].endswith("preview_phase3_probe.py")

    def test_a_complete_payload_validates_clean(self):
        assert probe.validate_payload(_payload()) == []

    @pytest.mark.parametrize("dropped", ["git_sha", "probe", "args", "per_click"])
    def test_a_missing_citation_field_is_reported(self, dropped):
        payload = _payload()
        del payload[dropped]
        problems = probe.validate_payload(payload)
        assert any(dropped in p for p in problems), problems

    def test_an_empty_sha_is_reported(self):
        payload = _payload()
        payload["git_sha"] = ""
        assert any("git_sha" in p for p in probe.validate_payload(payload))

    def test_missing_summary_boxes_are_reported(self):
        payload = _payload()
        del payload["summary"]["box1"]
        assert any("box1" in p for p in probe.validate_payload(payload))


class TestWriteJson:
    def test_round_trips_and_writes_lf_only(self, tmp_path):
        out = tmp_path / "artifact.json"
        probe.write_json(out, _payload())

        raw = out.read_bytes()
        # Windows text mode would rewrite every LF to CRLF, changing every
        # byte of an artifact that is meant to be hashed and diffed.
        assert b"\r\n" not in raw
        assert raw.endswith(b"\n")
        assert json.loads(raw.decode("utf-8"))["probe"] == probe.PROBE_NAME
