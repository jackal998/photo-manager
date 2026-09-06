"""Candidate generation + sampling for the visual ground-truth harness.

Each test here encodes a way the bootstrap can silently produce the wrong
label set: a run boundary off by one second, two cameras merged because
they share a shot second, a Live Photo ``.mov`` presented as a still to
rank, the same moment offered twice under two source tags, or a sample
that is not reproducible so Phase 2 cannot recompute against it.
"""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from scripts.visual_gt import bootstrap as bs


def _make_manifest(tmp_path: Path, rows) -> Path:
    """Build a manifest DB with only the columns ``read_manifest`` selects.

    ``rows`` items are ``(source_path, group_id, shot_date)``.
    """
    db = tmp_path / "manifest.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE migration_manifest ("
        " source_path TEXT, group_id TEXT, shot_date TEXT,"
        " file_size_bytes INTEGER, pixel_width INTEGER, pixel_height INTEGER)"
    )
    conn.executemany(
        "INSERT INTO migration_manifest VALUES (?,?,?,?,?,?)",
        [(path, gid, date, 1024, 4000, 3000) for path, gid, date in rows],
    )
    conn.commit()
    conn.close()
    return db


def _at(second: int) -> str:
    return f"2024-06-27T21:34:{second:02d}"


class TestRunSplitting:
    """The 3 s boundary is inclusive; 4 s breaks the run."""

    def test_exactly_three_second_gap_stays_one_run(self, tmp_path):
        db = _make_manifest(
            tmp_path,
            [(r"D:\pics\a.jpg", None, _at(0)), (r"D:\pics\b.jpg", None, _at(3))],
        )
        runs = bs.burst_runs(bs.read_manifest(db))
        assert [len(r.paths) for r in runs] == [2]

    def test_four_second_gap_breaks_the_run(self, tmp_path):
        db = _make_manifest(
            tmp_path,
            [(r"D:\pics\a.jpg", None, _at(0)), (r"D:\pics\b.jpg", None, _at(4))],
        )
        assert bs.burst_runs(bs.read_manifest(db)) == []

    def test_run_longer_than_the_cap_is_dropped(self, tmp_path):
        rows = [(rf"D:\pics\f{i:02d}.jpg", None, _at(i)) for i in range(13)]
        db = _make_manifest(tmp_path, rows)
        assert bs.burst_runs(bs.read_manifest(db)) == []

    def test_rows_without_a_shot_date_never_form_a_run(self, tmp_path):
        db = _make_manifest(
            tmp_path, [(r"D:\pics\a.jpg", None, None), (r"D:\pics\b.jpg", None, "")]
        )
        assert bs.burst_runs(bs.read_manifest(db)) == []


class TestCameraProxyRestriction:
    """Directory + extension stand in for camera identity (Make/Model are not persisted)."""

    def test_different_directories_do_not_join(self, tmp_path):
        db = _make_manifest(
            tmp_path,
            [(r"D:\pics\a.jpg", None, _at(0)), (r"D:\other\b.jpg", None, _at(1))],
        )
        assert bs.burst_runs(bs.read_manifest(db)) == []

    def test_different_extensions_do_not_join(self, tmp_path):
        db = _make_manifest(
            tmp_path,
            [(r"D:\pics\a.jpg", None, _at(0)), (r"D:\pics\b.heic", None, _at(1))],
        )
        assert bs.burst_runs(bs.read_manifest(db)) == []

    def test_same_directory_and_extension_join(self, tmp_path):
        db = _make_manifest(
            tmp_path,
            [(r"D:\pics\a.jpg", None, _at(0)), (r"D:\pics\b.jpg", None, _at(1))],
        )
        runs = bs.burst_runs(bs.read_manifest(db))
        assert [r.paths for r in runs] == [(r"D:\pics\a.jpg", r"D:\pics\b.jpg")]


class TestVideoExclusion:
    def test_live_photo_mov_passenger_is_dropped_from_a_dup_group(self, tmp_path):
        db = _make_manifest(
            tmp_path,
            [
                (r"D:\pics\IMG_1.jpg", "g1", _at(0)),
                (r"D:\pics\IMG_1.heic", "g1", _at(0)),
                (r"D:\pics\IMG_1.mov", "g1", _at(0)),
            ],
        )
        groups = bs.dup_groups(bs.read_manifest(db))
        assert len(groups) == 1
        assert not any(p.endswith(".mov") for p in groups[0].paths)
        assert len(groups[0].paths) == 2

    def test_a_video_only_burst_produces_nothing(self, tmp_path):
        db = _make_manifest(
            tmp_path,
            [(r"D:\pics\a.mov", None, _at(0)), (r"D:\pics\b.mov", None, _at(1))],
        )
        assert bs.build_candidate_pool(bs.read_manifest(db)) == []


class TestDupBurstDeduplication:
    def test_identical_member_set_is_emitted_once_as_dup(self, tmp_path):
        db = _make_manifest(
            tmp_path,
            [(r"D:\pics\a.jpg", "g1", _at(0)), (r"D:\pics\b.jpg", "g1", _at(1))],
        )
        pool = bs.build_candidate_pool(bs.read_manifest(db))
        assert len(pool) == 1
        assert pool[0].source == "dup"

    def test_a_burst_that_is_not_a_dup_group_survives(self, tmp_path):
        db = _make_manifest(
            tmp_path,
            [
                (r"D:\pics\a.jpg", "g1", _at(0)),
                (r"D:\pics\b.jpg", "g1", _at(1)),
                (r"D:\pics\c.jpg", None, _at(2)),
            ],
        )
        pool = bs.build_candidate_pool(bs.read_manifest(db))
        sources = sorted(g.source for g in pool)
        # dup {a,b} plus the burst run {a,b,c}, which is a different member set.
        assert sources == ["burst", "dup"]

    def test_group_key_is_order_independent(self):
        left = bs._group_key("dup", ["b", "a"])
        right = bs._group_key("dup", ["a", "b"])
        assert left == right and left.startswith("dup:")


class TestStratification:
    def _group(self, paths, epochs=None):
        return bs.CandidateGroup(
            key=bs._group_key("burst", paths),
            source="burst",
            paths=tuple(paths),
            epochs=tuple(epochs or [0] * len(paths)),
        )

    def test_long_exposure_wins_over_burst_length(self):
        group = self._group(["a.jpg", "b.jpg"])
        enrichment = {"a.jpg": bs.Enrichment(exposure_time=0.5)}
        assert bs.stratum_of(group, enrichment) == "long_exposure"

    def test_two_stop_spread_in_one_second_is_a_bracket(self):
        group = self._group(["a.jpg", "b.jpg"], epochs=[100, 101])
        enrichment = {
            "a.jpg": bs.Enrichment(exposure_time=0.01),
            "b.jpg": bs.Enrichment(exposure_time=0.04),
        }
        assert bs.stratum_of(group, enrichment) == "bracket"

    def test_same_spread_spread_over_minutes_is_not_a_bracket(self):
        group = self._group(["a.jpg", "b.jpg"], epochs=[100, 200])
        enrichment = {
            "a.jpg": bs.Enrichment(exposure_time=0.01),
            "b.jpg": bs.Enrichment(exposure_time=0.04),
        }
        assert bs.stratum_of(group, enrichment) == "burst_len_2"

    def test_burst_uuid_beats_length(self):
        group = self._group(["a.jpg", "b.jpg"])
        assert bs.stratum_of(group, {"b.jpg": bs.Enrichment(burst_uuid="U1")}) == "burst_id"

    def test_lengths_bucket_by_size(self):
        assert bs.stratum_of(self._group(["a", "b", "c"]), {}) == "burst_len_3_5"
        assert bs.stratum_of(self._group(list("abcdefg")), {}) == "burst_len_6_12"

    def test_dup_source_never_lands_in_a_burst_length_bucket(self):
        group = bs.CandidateGroup(key="dup:x", source="dup", paths=("a", "b"), epochs=(0, 0))
        assert bs.stratum_of(group, {}) == "dup"


class TestPathKeyMismatch:
    """exiftool's SourceFile uses '/' on Windows; the manifest uses '\\'."""

    def test_enrichment_keyed_by_exiftool_reaches_a_backslash_manifest_path(self):
        manifest_path = r"H:\Photos\2025\IMG_1.DNG"
        enrichment = {"H:/Photos/2025/IMG_1.DNG": bs.Enrichment(exposure_time=0.5)}
        assert bs.enrichment_for(enrichment, manifest_path).exposure_time == 0.5

    def test_the_stratifier_sees_that_enrichment(self):
        manifest_paths = (r"H:\Photos\2025\IMG_1.DNG", r"H:\Photos\2025\IMG_2.DNG")
        group = bs.CandidateGroup(
            key="burst:x", source="burst", paths=manifest_paths, epochs=(0, 1)
        )
        enrichment = {"H:/Photos/2025/IMG_1.DNG": bs.Enrichment(burst_uuid="U1")}
        assert bs.stratum_of(group, enrichment) == "burst_id"

    def test_member_payload_carries_the_enriched_values(self):
        photo = bs.ManifestPhoto(
            path=r"H:\a.jpg", group_id=None, shot_date=None, epoch=None,
            file_size_bytes=1, pixel_width=2, pixel_height=3,
        )
        payload = bs._member_payload(
            {photo.path: photo}, {"H:/a.jpg": bs.Enrichment(iso=400)}, photo.path
        )
        assert payload["iso"] == 400


class TestQuotas:
    def test_a_short_stratum_hands_its_share_to_the_others(self):
        sizes = {name: 0 for name in bs.STRATA}
        sizes["dup"] = 100
        sizes["burst_len_2"] = 2
        quotas = bs.allocate_quotas(sizes, 20)
        assert quotas["burst_len_2"] == 2
        assert quotas["dup"] == 18
        assert sum(quotas.values()) == 20

    def test_a_pool_smaller_than_the_request_is_taken_whole(self):
        sizes = {name: 0 for name in bs.STRATA}
        sizes["dup"] = 3
        assert sum(bs.allocate_quotas(sizes, 150).values()) == 3


class TestDeterminism:
    def _pool(self, count):
        return [
            bs.CandidateGroup(
                key=bs._group_key("burst", [f"f{i}_a.jpg", f"f{i}_b.jpg"]),
                source="burst",
                paths=(f"f{i}_a.jpg", f"f{i}_b.jpg"),
                epochs=(i, i),
            )
            for i in range(count)
        ]

    def test_same_seed_gives_the_same_sample_in_the_same_order(self):
        pool = self._pool(80)
        first, _, _ = bs.stratified_sample(pool, {}, total=20, seed=bs.DEFAULT_SEED)
        second, _, _ = bs.stratified_sample(pool, {}, total=20, seed=bs.DEFAULT_SEED)
        assert [g.key for g in first] == [g.key for g in second]
        assert len(first) == 20

    def test_sample_is_interleaved_so_a_prefix_covers_both_strata(self):
        pool = self._pool(40) + [
            bs.CandidateGroup(key=f"dup:{i}", source="dup", paths=(f"d{i}.jpg", "z.jpg"), epochs=(0, 0))
            for i in range(40)
        ]
        sample, _, _ = bs.stratified_sample(pool, {}, total=20, seed=1)
        prefix = {bs.stratum_of(g, {}) for g in sample[:4]}
        assert prefix == {"burst_len_2", "dup"}


class TestCsvResume:
    def test_missing_csv_reports_nothing_labelled(self, tmp_path):
        assert bs.read_labelled_keys(tmp_path / "nope.csv") == set()

    def test_row_set_sizes_ignore_the_precedence_comment_line(self, tmp_path):
        """The '#' note above the header must not be read as the header."""
        csv_path = tmp_path / "gt.csv"
        csv_path.write_text(
            "# visual-gt labels. Append-only log: the LAST COMPLETE row-set wins.\n"
            "group_key,source,case_tags,confidence,path,rank,excluded,labelled_at\n"
            "dup:aaa,dup,,clear winner,D:\\a.jpg,1,,2026-09-02T10:00:00.100000\n"
            "dup:aaa,dup,,clear winner,D:\\b.jpg,,1,2026-09-02T10:00:00.100000\n",
            encoding="utf-8",
        )
        assert bs.read_last_rowset_sizes(csv_path) == {"dup:aaa": 2}

    def test_only_the_last_row_set_counts(self, tmp_path):
        """An overwrite appends a second run; the earlier one must not add in."""
        csv_path = tmp_path / "gt.csv"
        csv_path.write_text(
            "group_key,source,case_tags,confidence,path,rank,excluded,labelled_at\n"
            "dup:aaa,dup,,clear winner,D:\\a.jpg,1,,2026-09-02T10:00:00.100000\n"
            "dup:aaa,dup,,clear winner,D:\\b.jpg,,1,2026-09-02T10:00:00.100000\n"
            "dup:aaa,dup,,toss-up,D:\\a.jpg,1,,2026-09-02T10:00:00.900000\n"
            "dup:aaa,dup,,toss-up,D:\\b.jpg,2,,2026-09-02T10:00:00.900000\n",
            encoding="utf-8",
        )
        assert bs.read_last_rowset_sizes(csv_path) == {"dup:aaa": 2}

    def test_a_truncated_last_row_reports_a_short_row_set(self, tmp_path):
        csv_path = tmp_path / "gt.csv"
        csv_path.write_text(
            "group_key,source,case_tags,confidence,path,rank,excluded,labelled_at\n"
            "dup:aaa,dup,,clear winner,D:\\a.jpg,1,,2026-09-02T10:00:00.100000\n"
            "dup:aaa,dup,,clear",
            encoding="utf-8",
        )
        assert bs.read_last_rowset_sizes(csv_path) == {"dup:aaa": 1}

    def test_existing_rows_are_reported_once_per_group(self, tmp_path):
        csv_path = tmp_path / "gt.csv"
        csv_path.write_text(
            "group_key,source,case_tags,confidence,path,rank,excluded,labelled_at\n"
            "dup:aaa,dup,,clear winner,D:\\a.jpg,1,,2026-09-02T10:00:00\n"
            "dup:aaa,dup,,clear winner,D:\\b.jpg,,1,2026-09-02T10:00:00\n",
            encoding="utf-8",
        )
        assert bs.read_labelled_keys(csv_path) == {"dup:aaa"}


class TestExiftoolParsing:
    def test_stderr_appended_after_the_json_array_is_tolerated(self):
        text = '[{"SourceFile":"a.jpg","ExposureTime":0.5}]\n    1 image files read'
        assert bs._parse_exiftool_json(text)[0]["ExposureTime"] == 0.5

    def test_a_bracket_in_the_appended_stderr_does_not_lose_the_batch(self):
        """execute() appends stderr; slicing to the LAST ']' drops 200 records."""
        text = (
            '[{"SourceFile":"a.jpg","ISO":100}]\n'
            "Warning: [minor] Bad IFD in a.jpg\n    1 image files read"
        )
        records = bs._parse_exiftool_json(text)
        assert [r["SourceFile"] for r in records] == ["a.jpg"]

    def test_unparseable_output_yields_no_records(self):
        assert bs._parse_exiftool_json("Error: File not found") == []

    def test_coverage_counts_only_present_signals(self):
        enrichment = {
            "a": bs.Enrichment(burst_uuid="U", subsec="03"),
            "b": bs.Enrichment(exposure_time=0.02),
        }
        counts = bs.coverage_counts(enrichment)
        assert counts["files_enriched"] == 2
        assert counts["burst_uuid"] == 1
        assert counts["subsec_time_original"] == 1
        assert counts["exposure_time"] == 1


class TestMissingFiles:
    """The manifest is a snapshot; deleted files must not reach the labeller."""

    def test_only_paths_on_disk_survive(self, tmp_path):
        real = tmp_path / "here.jpg"
        real.write_bytes(b"x")
        gone = tmp_path / "gone.jpg"
        assert bs.existing_paths([str(real), str(gone)]) == {str(real)}

    def test_cli_drops_groups_whose_files_are_gone(self, tmp_path, capsys):
        live_a = tmp_path / "a.jpg"
        live_b = tmp_path / "b.jpg"
        for path in (live_a, live_b):
            path.write_bytes(b"x")
        db = _make_manifest(
            tmp_path,
            [
                (str(live_a), "g1", _at(0)),
                (str(live_b), "g1", _at(1)),
                (str(tmp_path / "ghost1.jpg"), "g2", _at(0)),
                (str(tmp_path / "ghost2.jpg"), "g2", _at(1)),
            ],
        )
        out = tmp_path / "groups.json"
        assert bs.main(["--manifest", str(db), "--out", str(out), "--no-exiftool"]) == 0
        document = json.loads(out.read_text(encoding="utf-8"))
        served = {m["path"] for g in document["groups"] for m in g["members"]}
        assert served == {str(live_a), str(live_b)}
        assert "dropped 2 rows" in capsys.readouterr().out

    def test_keep_missing_opts_back_in(self, tmp_path):
        db = _make_manifest(
            tmp_path,
            [
                (str(tmp_path / "ghost1.jpg"), "g2", _at(0)),
                (str(tmp_path / "ghost2.jpg"), "g2", _at(1)),
            ],
        )
        out = tmp_path / "groups.json"
        assert (
            bs.main(
                ["--manifest", str(db), "--out", str(out), "--no-exiftool", "--keep-missing"]
            )
            == 0
        )
        assert json.loads(out.read_text(encoding="utf-8"))["groups"]


class TestCli:
    def test_end_to_end_writes_a_reloadable_sidecar(self, tmp_path, capsys):
        db = _make_manifest(
            tmp_path,
            [
                (r"D:\pics\a.jpg", "g1", _at(0)),
                (r"D:\pics\b.jpg", "g1", _at(1)),
                (r"D:\pics\c.jpg", None, _at(30)),
                (r"D:\pics\d.jpg", None, _at(31)),
            ],
        )
        out = tmp_path / "groups.json"
        code = bs.main(
            ["--manifest", str(db), "--out", str(out), "--no-exiftool", "--keep-missing", "--n", "5"]
        )
        assert code == 0
        document = json.loads(out.read_text(encoding="utf-8"))
        assert document["seed"] == bs.DEFAULT_SEED
        assert {g["source"] for g in document["groups"]} == {"dup", "burst"}
        assert all(m["name"] for g in document["groups"] for m in g["members"])
        assert "stratum counts" in capsys.readouterr().out

    def test_manifest_is_opened_read_only(self, tmp_path):
        db = _make_manifest(tmp_path, [(r"D:\pics\a.jpg", "g1", _at(0))])
        assert bs.manifest_uri(db).endswith("?mode=ro&immutable=1")
        conn = sqlite3.connect(bs.manifest_uri(db), uri=True)
        try:
            import pytest

            with pytest.raises(sqlite3.OperationalError):
                conn.execute("DELETE FROM migration_manifest")
        finally:
            conn.close()
