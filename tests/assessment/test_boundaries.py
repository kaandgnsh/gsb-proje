import ast
from pathlib import Path

from lai.assessment.service import create_job, run_job, submit_single
from lai.store import db
from lai.store.store import Store

from .conftest import build_store, csv_file


def test_assessment_layer_imports_no_ai_qt_network_or_process_control():
    src = Path(__file__).resolve().parents[2] / "src" / "lai" / "assessment"
    forbidden = (
        "PySide6",
        "lai.ai",
        "lai_runtime",
        "socket",
        "urllib",
        "http",
        "requests",
        "subprocess",
    )
    for path in src.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            assert not any(name.startswith(forbidden) for name in names), path


def test_v5_upgrade_preserves_m7_assessment_and_enables_jobs(tmp_path, monkeypatch):
    with monkeypatch.context() as old:
        old.setattr(db, "MIGRATIONS", db.MIGRATIONS[:5])
        old.setattr(db, "LATEST_VERSION", 5)
        s, _i, v, _pid, pv = build_store(tmp_path / "upgrade.db")
        saved = submit_single(
            s,
            inventory_version_id=v,
            profile_version_id=pv,
            responses={n: "A" for n in range(1, 9)},
            participant_code="SYN-OLD",
        )
        before = s.assessments.get(saved["assessment_id"])
        assert s.conn.execute("PRAGMA user_version").fetchone()[0] == 5
        s.close()
    upgraded = Store.open(tmp_path / "upgrade.db", backup_dir=tmp_path / "backups")
    try:
        assert upgraded.conn.execute("PRAGMA user_version").fetchone()[0] == 6
        assert upgraded.assessments.get(before.id) == before
        assert list((tmp_path / "backups").glob("*.db"))
        job = create_job(
            upgraded,
            csv_file(tmp_path / "new.csv"),
            inventory_version_id=v,
            profile_version_id=pv,
        )
        assert run_job(upgraded, job["id"])["counts"]["succeeded"] == 3
        assert upgraded.assessments.get(before.id) == before
    finally:
        upgraded.close()
