"""Korunan süreçte model olmadan belge → tekil/toplu → kayıt → bütün raporlar."""

import csv
import json
import sys
from pathlib import Path

from synthetic_desktop import ready

from lai.assessment.service import create_job, run_job, submit_single
from lai.reports.service import export_assessment, export_job
from lai.store.store import Store
from lai_runtime.sandbox import require_isolation


def main():
    kernel = require_isolation()
    work = Path(sys.argv[1])
    db = work / "offline.db"
    _, iv, _, pv = ready(db)
    store = Store.open(db)
    try:
        result = submit_single(
            store,
            inventory_version_id=iv,
            profile_version_id=pv,
            participant_code="Çığ-sentetik",
            responses=[(n, "A") for n in range(1, 9)],
        )
        source = work / "answers.csv"
        with source.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["participant_code", *[f"Q{n}" for n in range(1, 9)]])
            w.writerows([[f"SYN-{j}", *["A"] * 8] for j in range(3)])
        job = create_job(store, source, inventory_version_id=iv, profile_version_id=pv)
        done = run_job(store, job["id"])
        assert done["counts"]["succeeded"] == 3
        for fmt in ["pdf", "xlsx", "csv", "json"]:
            export_assessment(
                store, result["assessment_id"], work / f"single.{fmt}", fmt
            )
        for fmt in ["pdf", "xlsx", "csv"]:
            export_job(
                store, job["id"], work / f"batch.{'zip' if fmt == 'pdf' else fmt}", fmt
            )
        count = store.conn.execute("SELECT count(*) FROM assessments").fetchone()[0]
        assert count == 4
    finally:
        store.close()
    store = Store.open(db)
    assert store.assessments.get(result["assessment_id"]).results == result["result"]
    store.close()
    print(
        json.dumps(
            {
                "kernel": kernel,
                "imported": True,
                "single": True,
                "batch": 3,
                "stored": count,
                "reopened": True,
                "exports": ["pdf", "xlsx", "csv", "json"],
                "model_used": False,
            }
        )
    )


if __name__ == "__main__":
    main()
