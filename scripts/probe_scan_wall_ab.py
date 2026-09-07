"""Wall-time A/B driver for scanner changes (#786 gate; probe toolkit #784).

Runs one full scan pipeline imported from an arbitrary worktree root and
records wall time + manifest row count, so two builds can be compared on
the same target with interleaved (ABBA) runs:

    python scripts/probe_scan_wall_ab.py <worktree_root> <target_dir> <out_db> <result_json>

The manifest row count doubles as a correctness gate — both builds must
produce identical counts on every run before any speedup claim.
"""
import json
import sys
import time
from pathlib import Path

root = Path(sys.argv[1]).resolve()
target = sys.argv[2]
out_db = Path(sys.argv[3])
result_json = Path(sys.argv[4])

sys.path.insert(0, str(root))

from core.app_service.cancel_token import _CancelToken  # noqa: E402
from core.app_service.dtos import ScanConfig  # noqa: E402
from core.app_service.scan_runner import run_pipeline  # noqa: E402


class SpyBus:
    def __init__(self):
        self.finished_path = None
        self.error = None

    def log(self, msg):
        pass

    def stage(self, stage_name, completed, total, files_per_sec):
        pass

    def failed(self, msg):
        self.error = msg

    def finished(self, output_path):
        self.finished_path = output_path

    def completed_empty(self):
        self.error = "completed_empty"

    def hash_pool_measured(self, rates):
        pass

    def read_knee_measured(self, summary):
        pass


cfg = ScanConfig(sources={"ab": Path(target)}, output_path=out_db)
bus = SpyBus()
tok = _CancelToken()

t0 = time.perf_counter()
run_pipeline(cfg, tok, bus)
wall = time.perf_counter() - t0

rows = None
if bus.finished_path:
    import sqlite3

    con = sqlite3.connect(bus.finished_path)
    rows = con.execute("SELECT COUNT(*) FROM migration_manifest").fetchone()[0]
    con.close()

out = {
    "root": str(root),
    "target": target,
    "wall_s": round(wall, 2),
    "finished": bus.finished_path,
    "manifest_rows": rows,
    "error": bus.error,
}
result_json.write_text(json.dumps(out))
print(json.dumps(out, ensure_ascii=False))
