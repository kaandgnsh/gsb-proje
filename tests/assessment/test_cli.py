import json

from lai.learning.__main__ import main

from .conftest import csv_file


def test_cli_single_status_and_optional_contact(saved, capsys):
    s, _i, v, _pid, pv = saved
    args = [
        "assess",
        "--db",
        str(s.path),
        "--inventory-version",
        str(v),
        "--profile-version",
        str(pv),
        "--alias",
        "Sentetik Anonim",
        "--responses",
        json.dumps([[n, "A"] for n in range(1, 9)]),
        "--json",
    ]
    assert main(args) == 0
    r = json.loads(capsys.readouterr().out)
    assert r["assessment_id"] and r["result"]["status"] == "VERIFIED"
    assert s.participants.get(r["participant_id"]).full_name is None


def test_cli_batch_prepare_status_resume_cancel_and_list(saved, response_file, capsys):
    s, _i, v, _pid, pv = saved
    args = ["--db", str(s.path), "--json"]
    assert (
        main(
            [
                "batch",
                *args,
                "--inventory-version",
                str(v),
                "--profile-version",
                str(pv),
                str(response_file),
                "--prepare-only",
            ]
        )
        == 0
    )
    prepared = json.loads(capsys.readouterr().out)
    jobid = str(prepared["id"])
    assert main(["job", "list", *args]) == 0
    assert json.loads(capsys.readouterr().out)["jobs"][0]["id"] == prepared["id"]
    assert main(["job", "run", *args, "--id", jobid, "--limit", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "paused"
    assert main(["job", "cancel", *args, "--id", jobid]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "cancelled"
    assert main(["job", "run", *args, "--id", jobid]) == 0
    assert json.loads(capsys.readouterr().out)["counts"]["succeeded"] == 3
    assert main(["job", "status", *args, "--id", jobid]) == 0
    assert json.loads(capsys.readouterr().out)["progress"] == 1


def test_cli_turkish_errors_and_bad_row_counts(saved, tmp_path, capsys):
    s, _i, v, _pid, pv = saved
    p = csv_file(
        tmp_path / "Hatalı satır.csv", rows=[["P", "Z", *["A"] * 7], ["Q", *["A"] * 8]]
    )
    assert (
        main(
            [
                "batch",
                "--db",
                str(s.path),
                "--inventory-version",
                str(v),
                "--profile-version",
                str(pv),
                str(p),
                "--json",
            ]
        )
        == 1
    )
    result = json.loads(capsys.readouterr().out)
    assert result["counts"]["failed"] == 1 and result["counts"]["succeeded"] == 1
    assert result["rows"][0]["errors"][0]["question"] == 1
    assert main(["job", "status", "--db", str(s.path), "--json"]) == 2
    assert "--id" in json.loads(capsys.readouterr().out)["error"]


def test_cli_human_output_shows_result_and_counts(saved, response_file, capsys):
    s, _i, v, _pid, pv = saved
    assert (
        main(
            [
                "batch",
                "--db",
                str(s.path),
                "--inventory-version",
                str(v),
                "--profile-version",
                str(pv),
                str(response_file),
            ]
        )
        == 0
    )
    text = capsys.readouterr()
    assert "başarılı=3" in text.out and "bekleyen=0" in text.out
    assert "Toplam çağrı süresi" in text.out


def test_cli_json_answer_file_and_duplicate_response_rejection(saved, tmp_path, capsys):
    s, _i, v, _pid, pv = saved
    p = tmp_path / "answers.json"
    p.write_text('[[1,"A"],[1,"B"]]')
    assert (
        main(
            [
                "assess",
                "--db",
                str(s.path),
                "--inventory-version",
                str(v),
                "--profile-version",
                str(pv),
                "--participant-code",
                "P",
                "--answers",
                str(p),
                "--json",
            ]
        )
        == 2
    )
    assert "yinelenen" in json.loads(capsys.readouterr().out)["error"]
    assert not s.participants.list()


def test_cli_rescore_and_clone_job_are_separate(saved, response_file, capsys):
    s, _i, v, _pid, pv = saved
    args = ["--db", str(s.path), "--json"]
    assert (
        main(
            [
                "batch",
                *args,
                "--inventory-version",
                str(v),
                "--profile-version",
                str(pv),
                str(response_file),
            ]
        )
        == 0
    )
    first = json.loads(capsys.readouterr().out)
    assert (
        main(
            [
                "rescore",
                *args,
                "--assessment",
                str(first["rows"][0]["assessment_id"]),
                "--profile-version",
                str(pv),
            ]
        )
        == 0
    )
    second = json.loads(capsys.readouterr().out)
    assert second["assessment_id"] != first["rows"][0]["assessment_id"]
    assert (
        main(
            [
                "job",
                "clone",
                *args,
                "--id",
                str(first["id"]),
                "--profile-version",
                str(pv),
            ]
        )
        == 0
    )
    clone = json.loads(capsys.readouterr().out)
    assert (
        clone["id"] != first["id"] and clone["recalculated_from_job_id"] == first["id"]
    )


def test_cli_structural_file_error_has_context_and_no_job(saved, tmp_path, capsys):
    s, _i, v, _pid, pv = saved
    p = csv_file(tmp_path / "bad.csv", headers=["Who", "Q1"], rows=[["P", "A"]])
    assert (
        main(
            [
                "batch",
                "--db",
                str(s.path),
                "--inventory-version",
                str(v),
                "--profile-version",
                str(pv),
                str(p),
                "--json",
            ]
        )
        == 2
    )
    result = json.loads(capsys.readouterr().out)
    assert result["issues"][0]["file"] == "bad.csv"
    assert s.conn.execute("SELECT count(*) FROM assessment_jobs").fetchone()[0] == 0


def test_cli_result_exposes_saved_trace_and_profile_version(saved, capsys):
    from lai.assessment.service import submit_single

    s, _i, v, _pid, pv = saved
    saved_result = submit_single(
        s,
        inventory_version_id=v,
        profile_version_id=pv,
        responses={n: "A" for n in range(1, 9)},
        participant_code="P",
    )
    assert (
        main(
            [
                "result",
                "--db",
                str(s.path),
                "--assessment",
                str(saved_result["assessment_id"]),
                "--json",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert (
        result["results"] == saved_result["result"]
        and result["profile_version_id"] == pv
    )
    assert result["results"]["dimensions"][0]["contributions"]


def test_cli_duplicate_mapping_json_and_invalid_json_are_rejected(
    saved, tmp_path, capsys
):
    s, _i, v, _pid, pv = saved
    p = tmp_path / "mapping.json"
    p.write_text('{"questions":{},"questions":{}}')
    source = csv_file(tmp_path / "answers.csv")
    assert (
        main(
            [
                "batch",
                "--db",
                str(s.path),
                "--inventory-version",
                str(v),
                "--profile-version",
                str(pv),
                str(source),
                "--mapping",
                str(p),
                "--json",
            ]
        )
        == 2
    )
    assert "yinelenen" in json.loads(capsys.readouterr().out)["error"]
    assert s.conn.execute("SELECT count(*) FROM assessment_jobs").fetchone()[0] == 0
