import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from lai.assessment.service import (
    control_job,
    create_job,
    job_status,
    rescore,
    run_job,
    submit_single,
)
from lai.core.scoring import ScoringInputError
from lai.scoring import evaluate
from lai.store.store import Store

from .conftest import csv_file


def test_single_and_batch_use_identical_m7_result(saved, response_file):
    s, _i, v, _pid, pv = saved
    answers = [[n, "A"] for n in range(1, 9)]
    one = submit_single(
        s,
        inventory_version_id=v,
        profile_version_id=pv,
        responses=answers,
        alias="Anonim Deniz",
    )
    assert s.participants.get(one["participant_id"]).full_name is None
    assert (
        one["result"]
        == evaluate(
            s, inventory_version_id=v, profile_version_id=pv, responses=answers
        ).to_dict()
    )
    job = create_job(s, response_file, inventory_version_id=v, profile_version_id=pv)
    done = run_job(s, job["id"])
    assert done["state"] == "completed" and done["counts"] == {
        "succeeded": 3,
        "partial": 0,
        "failed": 0,
        "pending": 0,
    }
    assert all(
        s.assessments.get(r["assessment_id"]).results == one["result"]
        for r in done["rows"]
    )


@pytest.mark.parametrize("identity", [{}, {"participant_code": " "}, {"alias": ""}])
def test_single_requires_only_code_or_alias_and_no_orphans(saved, identity):
    s, _i, v, _pid, pv = saved
    before = s.conn.execute("SELECT count(*) FROM participants").fetchone()[0]
    with pytest.raises(ValueError):
        submit_single(
            s, inventory_version_id=v, profile_version_id=pv, responses=[], **identity
        )
    assert s.conn.execute("SELECT count(*) FROM participants").fetchone()[0] == before


def test_invalid_duplicate_single_answers_rollback_all(saved):
    s, _i, v, _pid, pv = saved
    with pytest.raises(ScoringInputError):
        submit_single(
            s,
            inventory_version_id=v,
            profile_version_id=pv,
            responses=[(1, "A"), (1, "B")],
            participant_code="P",
        )
    assert not s.participants.list()
    assert s.conn.execute("SELECT count(*) FROM assessments").fetchone()[0] == 0


def test_bad_rows_dont_block_good_rows_and_empty_is_m7_missing(saved, tmp_path):
    s, _i, v, _pid, pv = saved
    p = csv_file(
        tmp_path / "errors.csv",
        rows=[["OK", *["A"] * 8], ["BAD", "Z", *["A"] * 7], ["EMPTY", "", *["A"] * 7]],
    )
    job = create_job(s, p, inventory_version_id=v, profile_version_id=pv)
    assert job["counts"]["failed"] == 1 and not s.participants.list()
    done = run_job(s, job["id"])
    assert done["counts"] == {"succeeded": 2, "partial": 0, "failed": 1, "pending": 0}
    assert s.participants.find_by_code("BAD") is None
    empty = s.assessments.get(done["rows"][2]["assessment_id"])
    assert (
        empty.results
        == evaluate(
            s,
            inventory_version_id=v,
            profile_version_id=pv,
            responses={1: None, **{n: "A" for n in range(2, 9)}},
        ).to_dict()
    )


def test_partial_blocked_results_are_saved_without_default_points(saved, response_file):
    s, i, v, pid, _pv = saved
    s.questions.upsert_requirement(
        i,
        req_key="critical",
        category="other",
        description="Eksik",
        critical=True,
        status="CONTRADICTORY",
        reason="sentetik",
        dimension_code="E-I",
    )
    new = s.profiles.publish_version(pid)
    job = create_job(
        s, response_file, inventory_version_id=v, profile_version_id=new.id
    )
    done = run_job(s, job["id"])
    assert done["counts"]["partial"] == 3
    d = s.assessments.get(done["rows"][0]["assessment_id"]).results["dimensions"]
    assert d[0]["raw"] is None and d[0]["selected_pole"] is None
    assert d[1]["raw"] is not None


def test_pause_cancel_resume_fixed_inputs_and_versions(saved, response_file):
    s, i, v, pid, pv = saved
    job = create_job(s, response_file, inventory_version_id=v, profile_version_id=pv)
    paused = run_job(s, job["id"], limit=1)
    assert paused["state"] == "paused" and paused["counts"]["pending"] == 2
    first = paused["rows"][0]["assessment_id"]
    control_job(s, job["id"], "cancel")
    s.knowledge.apply_user_correction(
        i,
        entry_key="rule:default_option_map:dim:E-I",
        content={"body": {"dimension": "E-I", "options": {"A": "I", "B": "E"}}},
        summary="Yeni",
        explanation="Sentetik düzeltme",
        user_text="Değiştir",
    )
    new = s.profiles.publish_version(pid)
    response_file.write_text("dosya artık farklı")
    other = Store.open(s.path)
    try:
        done = run_job(other, job["id"])
        assert done["profile_version_id"] == pv and done["inventory_version_id"] == v
        assert done["rows"][0]["assessment_id"] == first
        assert len({r["assessment_id"] for r in done["rows"]}) == 3
        old = s.assessments.get(first).results
        assert all(
            other.assessments.get(r["assessment_id"]).results == old
            for r in done["rows"]
        )
        assert new.id != pv
        assert run_job(other, job["id"])["counts"] == done["counts"]
    finally:
        other.close()


def test_crash_between_assessment_and_checkpoint_rolls_back_current_participant(
    saved, response_file, monkeypatch
):
    s, _i, v, _pid, pv = saved
    job = create_job(s, response_file, inventory_version_id=v, profile_version_id=pv)
    original = s.assessments.record

    def crash(**kwargs):
        original(**kwargs)
        raise KeyboardInterrupt()

    monkeypatch.setattr(s.assessments, "record", crash)
    with pytest.raises(KeyboardInterrupt):
        run_job(s, job["id"])
    assert job_status(s, job["id"])["state"] == "paused"
    assert job_status(s, job["id"])["counts"]["pending"] == 3
    assert not s.participants.list()
    assert s.conn.execute("SELECT count(*) FROM assessments").fetchone()[0] == 0
    monkeypatch.setattr(s.assessments, "record", original)
    assert run_job(s, job["id"])["counts"]["succeeded"] == 3


@pytest.mark.parametrize("error_type", [ValueError, RuntimeError])
def test_error_after_write_isolated_with_savepoint(
    saved, response_file, monkeypatch, error_type
):
    s, _i, v, _pid, pv = saved
    job = create_job(s, response_file, inventory_version_id=v, profile_version_id=pv)
    original = s.assessments.record
    calls = 0

    def failure(**kwargs):
        nonlocal calls
        calls += 1
        result = original(**kwargs)
        if calls == 2:
            raise error_type("Sentetik katılımcı hatası")
        return result

    monkeypatch.setattr(s.assessments, "record", failure)
    done = run_job(s, job["id"])
    assert done["counts"] == {"succeeded": 2, "partial": 0, "failed": 1, "pending": 0}
    assert len(s.participants.list()) == 2
    assert s.conn.execute("SELECT count(*) FROM response_sets").fetchone()[0] == 2


def test_external_cancel_applies_at_participant_boundary(saved, response_file):
    s, _i, v, _pid, pv = saved
    job = create_job(s, response_file, inventory_version_id=v, profile_version_id=pv)
    other = Store.open(s.path)
    try:

        def progress(info):
            control_job(other, job["id"], "cancel")

        done = run_job(s, job["id"], progress=progress)
        assert done["state"] == "cancelled" and done["counts"]["succeeded"] == 1
        assert run_job(other, job["id"])["counts"]["succeeded"] == 3
    finally:
        other.close()


def test_two_workers_cannot_duplicate_completed_participants(saved, response_file):
    s, _i, v, _pid, pv = saved
    job = create_job(s, response_file, inventory_version_id=v, profile_version_id=pv)
    barrier = threading.Barrier(2)

    def worker():
        db = Store.open(s.path)
        try:
            barrier.wait()
            return run_job(db, job["id"])
        finally:
            db.close()

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: worker(), range(2)))
    assert job_status(s, job["id"])["counts"]["succeeded"] == 3
    assert s.conn.execute("SELECT count(*) FROM assessments").fetchone()[0] == 3
    assert all(r["counts"]["pending"] == 0 for r in results)


def test_rescore_creates_new_record_and_preserves_old(saved):
    s, i, v, pid, pv = saved
    one = submit_single(
        s,
        inventory_version_id=v,
        profile_version_id=pv,
        responses={n: "A" for n in range(1, 9)},
        participant_code="P",
    )
    old = s.assessments.get(one["assessment_id"])
    s.knowledge.apply_user_correction(
        i,
        entry_key="rule:default_option_map:dim:E-I",
        content={"body": {"dimension": "E-I", "options": {"A": "I", "B": "E"}}},
        summary="Yeni",
        explanation="Sentetik",
        user_text="Değiştir",
    )
    new = s.profiles.publish_version(pid)
    second = rescore(s, old.id, new.id)
    assert second["assessment_id"] != old.id
    assert s.assessments.get(second["assessment_id"]).recalculated_from == old.id
    assert s.assessments.get(old.id) == old
    assert second["participant_id"] == one["participant_id"]


def test_immutable_job_versions_source_inputs_and_completed_rows(saved, response_file):
    s, _i, v, _pid, pv = saved
    job = create_job(s, response_file, inventory_version_id=v, profile_version_id=pv)
    for sql in [
        "UPDATE assessment_jobs SET profile_version_id=999",
        "UPDATE assessment_job_rows SET answers='[]'",
        "DELETE FROM assessment_job_rows",
    ]:
        with pytest.raises(sqlite3.IntegrityError):
            s.conn.execute(sql)
    run_job(s, job["id"])
    with pytest.raises(sqlite3.IntegrityError):
        s.conn.execute(
            "UPDATE assessment_job_rows SET state='pending',assessment_id=NULL"
        )


def test_alias_ambiguity_and_code_conflict_are_row_errors(saved, tmp_path):
    s, _i, v, _pid, pv = saved
    s.participants.create(code="ONE", alias="Aynı")
    s.participants.create(code="TWO", alias="Aynı")
    p = csv_file(
        tmp_path / "aliases.csv",
        headers=["alias", *[f"Q{i}" for i in range(1, 9)]],
        rows=[["Aynı", *["A"] * 8], ["Yeni", *["A"] * 8]],
    )
    done = run_job(
        s, create_job(s, p, inventory_version_id=v, profile_version_id=pv)["id"]
    )
    assert done["counts"]["failed"] == 1 and done["counts"]["succeeded"] == 1


def test_new_profile_clones_job_inputs_and_links_previous_results(saved, response_file):
    from lai.assessment.service import clone_job

    s, i, v, pid, pv = saved
    old = run_job(
        s,
        create_job(s, response_file, inventory_version_id=v, profile_version_id=pv)[
            "id"
        ],
    )
    before = [s.assessments.get(r["assessment_id"]) for r in old["rows"]]
    s.knowledge.apply_user_correction(
        i,
        entry_key="rule:default_option_map:dim:E-I",
        content={"body": {"dimension": "E-I", "options": {"A": "I", "B": "E"}}},
        summary="Yeni",
        explanation="Sentetik",
        user_text="Değiştir",
    )
    newer = s.profiles.publish_version(pid)
    response_file.unlink()
    clone = clone_job(s, old["id"], newer.id)
    assert (
        clone["recalculated_from_job_id"] == old["id"]
        and clone["counts"]["pending"] == 3
    )
    done = run_job(s, clone["id"])
    assert done["counts"]["succeeded"] == 3 and done["profile_version_id"] == newer.id
    for original, row in zip(before, done["rows"], strict=True):
        new = s.assessments.get(row["assessment_id"])
        assert new.recalculated_from == original.id and new.results != original.results
        assert s.assessments.get(original.id) == original
    assert job_status(s, old["id"])["state"] == "completed"


def test_process_death_rollback_and_resume_without_duplicate(saved, response_file):
    import os
    import subprocess
    import sys
    from pathlib import Path

    s, _i, v, _pid, pv = saved
    job = create_job(s, response_file, inventory_version_id=v, profile_version_id=pv)
    script = """
import os,sys
from pathlib import Path
from lai.store.store import Store
from lai.assessment.service import run_job
s=Store.open(Path(sys.argv[1]))
original=s.assessments.record
def die(**kw):
    original(**kw)
    os._exit(19)
s.assessments.record=die
run_job(s,int(sys.argv[2]))
"""
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")}
    result = subprocess.run(
        [sys.executable, "-c", script, str(s.path), str(job["id"])],
        env=env,
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 19, result.stderr.decode()
    assert job_status(s, job["id"])["state"] == "running"
    assert s.conn.execute("SELECT count(*) FROM assessments").fetchone()[0] == 0
    done = run_job(s, job["id"])
    assert done["counts"]["succeeded"] == 3
    assert s.conn.execute("SELECT count(*) FROM assessments").fetchone()[0] == 3


def test_other_connection_cannot_observe_result_without_checkpoint(
    saved, response_file, monkeypatch
):
    s, _i, v, _pid, pv = saved
    job = create_job(s, response_file, inventory_version_id=v, profile_version_id=pv)
    observer = Store.open(s.path)
    record = s.assessments.record

    def inspect(**kwargs):
        result = record(**kwargs)
        observed = job_status(observer, job["id"])
        assert (
            observer.conn.execute("SELECT count(*) FROM assessments").fetchone()[0]
            == observed["counts"]["succeeded"]
        )
        return result

    monkeypatch.setattr(s.assessments, "record", inspect)
    try:
        assert run_job(s, job["id"])["counts"]["succeeded"] == 3
    finally:
        observer.close()


def test_all_blocked_results_are_retained_as_partial(saved, response_file):
    s, i, v, pid, _pv = saved
    s.questions.upsert_requirement(
        i,
        req_key="global",
        category="other",
        description="Bilinmiyor",
        critical=True,
        status="UNKNOWN",
        reason="sentetik",
    )
    pv = s.profiles.publish_version(pid)
    done = run_job(
        s,
        create_job(s, response_file, inventory_version_id=v, profile_version_id=pv.id)[
            "id"
        ],
    )
    assert done["counts"]["partial"] == 3
    result = s.assessments.get(done["rows"][0]["assessment_id"])
    assert all(d["raw"] is None for d in result.results["dimensions"])
    assert result.limitations and result.results["full_profile"] is None


def test_mismatched_version_fails_before_staging_and_unknown_job(saved, response_file):
    s, i, _v, _pid, pv = saved
    v2 = s.inventory.add_version(i)
    with pytest.raises(ScoringInputError):
        create_job(s, response_file, inventory_version_id=v2, profile_version_id=pv)
    assert s.conn.execute("SELECT count(*) FROM assessment_jobs").fetchone()[0] == 0
    with pytest.raises(KeyError):
        run_job(s, 999)


def test_runtime_failure_retried_only_in_new_job(saved, response_file, monkeypatch):
    from lai.assessment.service import clone_job

    s, _i, v, _pid, pv = saved
    record = s.assessments.record

    def fail(**kwargs):
        raise ValueError("Sentetik kayıt sorunu")

    job = create_job(s, response_file, inventory_version_id=v, profile_version_id=pv)
    monkeypatch.setattr(s.assessments, "record", fail)
    assert run_job(s, job["id"])["counts"]["failed"] == 3
    monkeypatch.setattr(s.assessments, "record", record)
    assert run_job(s, job["id"])["counts"]["failed"] == 3
    new = clone_job(s, job["id"], pv)
    assert run_job(s, new["id"])["counts"]["succeeded"] == 3


def test_single_and_batch_empty_whitespace_answers_match(saved, tmp_path):
    s, _i, v, _pid, pv = saved
    answers = [[n, " A " if n > 1 else " "] for n in range(1, 9)]
    one = submit_single(
        s,
        inventory_version_id=v,
        profile_version_id=pv,
        responses=answers,
        participant_code="ONE",
    )
    p = csv_file(tmp_path / "spaces.csv", rows=[["TWO", *[a for _, a in answers]]])
    done = run_job(
        s, create_job(s, p, inventory_version_id=v, profile_version_id=pv)["id"]
    )
    assert s.assessments.get(done["rows"][0]["assessment_id"]).results == one["result"]


def test_motor_version_change_does_not_silently_resume(
    saved, response_file, monkeypatch
):
    from lai.assessment import service

    s, _i, v, _pid, pv = saved
    job = create_job(s, response_file, inventory_version_id=v, profile_version_id=pv)
    monkeypatch.setattr(service, "ENGINE_VERSION", "different-engine")
    with pytest.raises(ValueError, match="motor sürümü"):
        run_job(s, job["id"])
    assert job_status(s, job["id"])["counts"]["pending"] == 3


def test_completed_state_cannot_hide_pending_rows(saved, response_file):
    s, _i, v, _pid, pv = saved
    job = create_job(s, response_file, inventory_version_id=v, profile_version_id=pv)
    with pytest.raises(sqlite3.IntegrityError):
        s.conn.execute(
            "UPDATE assessment_jobs SET state='completed' WHERE id=?", (job["id"],)
        )


@pytest.mark.parametrize("fmt", ["csv", "xlsx"])
def test_no_network_is_needed_for_full_m8_pipeline(
    saved, response_file, monkeypatch, fmt
):
    import socket

    s, _i, v, _pid, pv = saved

    def no_socket(*a, **kwargs):
        raise AssertionError("Ağ yasak")

    if fmt == "xlsx":
        import csv

        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        with response_file.open(encoding="utf-8-sig", newline="") as source:
            for row in csv.reader(source):
                ws.append(row)
        response_file = response_file.with_suffix(".xlsx")
        wb.save(response_file)
    monkeypatch.setattr(socket, "socket", no_socket)
    job = create_job(s, response_file, inventory_version_id=v, profile_version_id=pv)
    assert run_job(s, job["id"])["counts"]["succeeded"] == 3


def test_storage_error_pauses_without_turning_participant_into_failed(
    saved, response_file, monkeypatch
):
    s, _i, v, _pid, pv = saved
    job = create_job(s, response_file, inventory_version_id=v, profile_version_id=pv)
    record = s.assessments.record

    def disk_error(**kwargs):
        record(**kwargs)
        raise OSError("Sentetik disk sorunu")

    monkeypatch.setattr(s.assessments, "record", disk_error)
    with pytest.raises(OSError):
        run_job(s, job["id"])
    assert job_status(s, job["id"])["state"] == "paused"
    assert job_status(s, job["id"])["counts"]["failed"] == 0
    assert job_status(s, job["id"])["counts"]["pending"] == 3
    assert s.conn.execute("SELECT count(*) FROM assessments").fetchone()[0] == 0
    monkeypatch.setattr(s.assessments, "record", record)
    assert run_job(s, job["id"])["counts"]["succeeded"] == 3
