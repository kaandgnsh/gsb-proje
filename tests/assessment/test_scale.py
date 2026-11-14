import csv
import time

import pytest
from openpyxl import Workbook
from synthetic_rules import true_outcome

from lai.assessment.service import create_job, run_job

from .conftest import build_store


@pytest.mark.parametrize("suffix", ["csv", "xlsx"])
def test_400_questions_200_participants_total_pipeline(tmp_path, suffix):
    n = 400
    people = 200
    s, _i, v, _pid, pv = build_store(tmp_path / "scale.db", n)
    path = tmp_path / ("sentetik-400x200." + suffix)
    rows = [
        [f"SYN-{j:03}", *["B" if (q + j) % 3 == 0 else "A" for q in range(1, n + 1)]]
        for j in range(people)
    ]
    headers = ["participant_id", *[f"Q{i}" for i in range(1, n + 1)]]
    if suffix == "csv":
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(headers)
            w.writerows(rows)
    else:
        wb = Workbook(write_only=True)
        ws = wb.create_sheet("Sentetik Yanıtlar")
        ws.append(headers)
        for row in rows:
            ws.append(row)
        wb.save(path)
    try:
        started = time.perf_counter()
        job = create_job(s, path, inventory_version_id=v, profile_version_id=pv)
        done = run_job(s, job["id"])
        elapsed = time.perf_counter() - started
        assert done["counts"]["succeeded"] == people and done["counts"]["pending"] == 0
        assert (
            s.conn.execute("SELECT count(*) FROM responses").fetchone()[0] == n * people
        )
        assert (
            s.conn.execute("SELECT count(*) FROM assessments").fetchone()[0] == people
        )
        reverse = set(range(7, n + 1, 7))
        for j, row in enumerate(done["rows"]):
            assessment = s.assessments.get(row["assessment_id"])
            expected = true_outcome({q: rows[j][q] for q in range(1, n + 1)}, reverse)
            for d in assessment.results["dimensions"]:
                assert d["raw"] == {
                    p: str(value)
                    for p, value in expected[d["dimension"]]["scores"].items()
                }
                assert d["selected_pole"] == expected[d["dimension"]]["letter"]
            assert (
                sum(len(d["contributions"]) for d in assessment.results["dimensions"])
                == n
            )
        assert elapsed < 30, f"{suffix}: {elapsed:.3f} saniye"
        print(
            f"\nM8 {suffix.upper()} 400×200: okuma+doğrulama+hesap+kayıt={elapsed:.6f} sn; hazırlık={done['preparation_seconds']:.6f}; işleme={done['processing_seconds']:.6f}"
        )
    finally:
        s.close()
