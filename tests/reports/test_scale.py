import csv
import time

from lai.assessment.service import create_job, run_job
from lai.reports.service import export_job
from tests.assessment.conftest import build_store


def test_large_export_progress_and_frozen_versions(tmp_path):
    store, _, iv, pid, pv = build_store(tmp_path / "scale.db", 400)
    try:
        source = tmp_path / "400x200.csv"
        with source.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["participant_code", *[f"Q{n}" for n in range(1, 401)]])
            w.writerows([[f"SYN-{n}", *["A"] * 400] for n in range(200)])
        job = create_job(store, source, inventory_version_id=iv, profile_version_id=pv)
        run_job(store, job["id"])
        store.profiles.publish_version(pid)
        for fmt in ["csv", "xlsx"]:
            progress = []
            start = time.perf_counter()
            output = tmp_path / f"400x200.{fmt}"
            result = export_job(store, job["id"], output, fmt, progress=progress.append)
            elapsed = time.perf_counter() - start
            assert result["counts"]["succeeded"] == 200
            assert progress[-1]["phase"] == "writing"
            assert progress[-1]["done"] == progress[-1]["total"]
            assert output.stat().st_size > 1000
            print(
                f"\nM10 {fmt} 400×200 kayıt okuma+raporlama+atomik yazma: {elapsed:.6f} sn; {output.stat().st_size} bayt"
            )
    finally:
        store.close()
