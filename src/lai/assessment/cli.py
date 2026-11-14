"""Mevcut öğrenme CLI'ı için Türkçe M8 komutları."""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

from .files import ResponseFileError
from .service import (
    clone_job,
    control_job,
    create_job,
    job_status,
    list_jobs,
    rescore,
    run_job,
    submit_single,
)

COMMANDS = ("assess", "batch", "job", "rescore", "result")


def add_commands(sub):
    parsers = {
        name: sub.add_parser(name, help=help)
        for name, help in (
            ("assess", "tek katılımcıyı değerlendir ve kaydet"),
            ("batch", "CSV/XLSX toplu değerlendirme"),
            ("job", "toplu iş durumu, duraklatma, iptal veya devam"),
            ("rescore", "eski sonucu yeni profille yeniden hesapla"),
            ("result", "kayıtlı puan ve hesap dökümünü göster"),
        )
    }
    for parser in parsers.values():
        parser.add_argument("--db", required=True)
        parser.add_argument("--json", action="store_true")
    for name in ("assess", "batch"):
        parsers[name].add_argument("--inventory-version", type=int, required=True)
        parsers[name].add_argument("--profile-version", type=int, required=True)
    single = parsers["assess"]
    single.add_argument("--participant-code")
    single.add_argument("--alias")
    single.add_argument("--full-name")
    single.add_argument("--email")
    single.add_argument("--phone")
    inp = single.add_mutually_exclusive_group(required=True)
    inp.add_argument(
        "--answers", help='JSON cevap çiftleri dosyası, örn. [[1,"A"],[2,null]]'
    )
    inp.add_argument("--responses", help="JSON cevap çiftleri")
    batch = parsers["batch"]
    batch.add_argument("file")
    batch.add_argument("--mapping", help="Sütun eşleme JSON dosyası")
    batch.add_argument("--sheet", action="append")
    batch.add_argument("--delimiter", choices=[",", ";", "\t"])
    batch.add_argument("--prepare-only", action="store_true")
    job = parsers["job"]
    job.add_argument(
        "action", choices=["list", "status", "run", "pause", "cancel", "clone"]
    )
    job.add_argument("--id", type=int)
    job.add_argument("--limit", type=int)
    job.add_argument(
        "--profile-version", type=int, help="clone için yeni profil sürümü"
    )
    parsers["result"].add_argument("--assessment", type=int, required=True)
    recalc = parsers["rescore"]
    recalc.add_argument("--assessment", type=int, required=True)
    recalc.add_argument("--profile-version", type=int, required=True)


def _unique_object(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"JSON içinde yinelenen alan: {key}")
        out[key] = value
    return out


def _read_json(path):
    if Path(path).stat().st_size > 50 * 1024 * 1024:
        raise ValueError("JSON dosyası 50 MiB sınırını aşıyor")
    return json.loads(
        Path(path).read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_object
    )


def _summary(result):
    if "results" in result:
        return f"Değerlendirme #{result['id']} [{result['status']}], profil sürümü={result['profile_version_id']}"
    if "jobs" in result:
        return (
            "\n".join(_summary(job) for job in result["jobs"])
            or "Kayıtlı toplu iş yok."
        )
    if "counts" in result:
        c = result["counts"]
        return (
            f"İş #{result['id']} [{result['state']}] {result['done']}/{result['total']}; "
            f"başarılı={c['succeeded']}, kısmi={c['partial']}, başarısız={c['failed']}, bekleyen={c['pending']}; "
            f"envanter sürümü={result['inventory_version_id']}, profil sürümü={result['profile_version_id']}"
        )
    return f"Değerlendirme #{result['assessment_id']}: {result['participant_code']} — {result['result']['status']}, profil={result['result']['full_profile'] or 'hesaplanamadı'}"


def run(a, store):
    started = time.perf_counter()

    def progress(result):
        if not a.json:
            print(_summary(result), file=sys.stderr, flush=True)

    active_job = None
    try:
        if a.cmd == "job" and a.action != "list" and a.id is None:
            raise ValueError("Bu işlem için --id gerekli")
        if a.cmd == "assess":
            responses = (
                _read_json(a.answers)
                if a.answers
                else json.loads(a.responses, object_pairs_hook=_unique_object)
            )
            if not isinstance(responses, list):
                raise ValueError(
                    "CLI cevapları çiftler listesi olmalı; yinelenenleri kaybetmeyin"
                )
            result = submit_single(
                store,
                inventory_version_id=a.inventory_version,
                profile_version_id=a.profile_version,
                responses=responses,
                participant_code=a.participant_code,
                alias=a.alias,
                full_name=a.full_name,
                email=a.email,
                phone=a.phone,
            )
        elif a.cmd == "batch":
            result = create_job(
                store,
                a.file,
                inventory_version_id=a.inventory_version,
                profile_version_id=a.profile_version,
                mapping=_read_json(a.mapping) if a.mapping else None,
                sheets=a.sheet,
                delimiter=a.delimiter,
            )
            active_job = result["id"]
            if not a.prepare_only:
                result = run_job(store, result["id"], progress=progress)
        elif a.cmd == "result":
            from dataclasses import asdict

            result = asdict(store.assessments.get(a.assessment))
        elif a.cmd == "rescore":
            result = rescore(store, a.assessment, a.profile_version)
        elif a.action == "list":
            result = {"jobs": list_jobs(store)}
        elif a.action == "clone":
            if a.profile_version is None:
                raise ValueError("clone için --profile-version gerekli")
            result = clone_job(store, a.id, a.profile_version)
        elif a.action == "status":
            result = job_status(store, a.id, include_rows=True)
        elif a.action == "run":
            active_job = a.id
            result = run_job(store, a.id, limit=a.limit, progress=progress)
        else:
            result = control_job(store, a.id, a.action)
        result["invocation_seconds"] = time.perf_counter() - started
    except KeyboardInterrupt:
        print(
            f"İşlem kesildi; tamamlanmış kayıtlar korunuyor. İş kimliği: {active_job or 'job list ile kontrol edin'}.",
            file=sys.stderr,
        )
        return 130
    except (ValueError, KeyError, OSError, sqlite3.Error, RuntimeError) as e:
        if a.json:
            print(
                json.dumps(
                    {
                        "error": str(e),
                        "issues": [vars(i) for i in e.issues]
                        if isinstance(e, ResponseFileError)
                        else [],
                    },
                    ensure_ascii=False,
                )
            )
        else:
            print(f"HATA: {e}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    else:
        print(_summary(result))
        if "result" in result or "results" in result:
            print(
                json.dumps(
                    result.get("result", result.get("results")),
                    ensure_ascii=False,
                    indent=1,
                )
            )
        for row in result.get("rows", []):
            if row["state"] == "partial":
                print(
                    f"KISMİ: satır={row['row_number']}, katılımcı={row['identity']}, değerlendirme=#{row['assessment_id']}: {row['limitations']}",
                    file=sys.stderr,
                )
            for error in row["errors"]:
                print(f"HATA: {error}", file=sys.stderr)
        print(f"Toplam çağrı süresi: {result['invocation_seconds']:.6f} saniye")
    return 1 if result.get("counts", {}).get("failed", 0) else 0
