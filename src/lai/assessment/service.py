"""M7 ortak girişinden atomik kayıt ve devam ettirilebilir SQLite işleri."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path

from lai.core.scoring import ENGINE_VERSION
from lai.scoring import evaluate

from .files import Issue, identity_values, normalize_responses, read_responses


def _json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _participant(store, identity):
    identity = identity_values(identity)
    code = identity.get("participant_code")
    alias = identity.get("alias")
    if code:
        p = store.participants.find_by_code(code)
        if p and alias and p.alias and p.alias != alias:
            raise ValueError("Katılımcı kodu farklı bir takma adla kayıtlı")
    else:
        matches = store.conn.execute(
            "SELECT id FROM participants WHERE alias=?", (alias,)
        ).fetchall()
        if len(matches) > 1:
            raise ValueError(
                "Takma ad birden fazla katılımcıyla eşleşiyor; kimlik kullanın"
            )
        p = store.participants.get(matches[0][0]) if matches else None
    if p:
        return p
    return store.participants.create(
        code=code or None,
        alias=alias or None,
        full_name=identity.get("full_name") or None,
        attributes={k: identity[k] for k in ("email", "phone") if identity.get(k)},
    )


def _limitations(result):
    return [note for d in result.dimensions for note in d.limitations]


def submit_single(
    store,
    *,
    inventory_version_id,
    profile_version_id,
    responses,
    participant_code=None,
    alias=None,
    full_name=None,
    email=None,
    phone=None,
    recalculated_from=None,
    source_label=None,
):
    """Başarılı kayıt tek işlem; hatalı girdi katılımcı/cevap/sonuç artığı bırakmaz."""
    identity = identity_values(
        {
            k: v
            for k, v in {
                "participant_code": participant_code,
                "alias": alias,
                "full_name": full_name,
                "email": email,
                "phone": phone,
            }.items()
            if v is not None
        }
    )
    responses = normalize_responses(responses)
    with store.transaction():
        result = evaluate(
            store,
            inventory_version_id=inventory_version_id,
            profile_version_id=profile_version_id,
            responses=responses,
        )
        p = _participant(store, identity)
        if recalculated_from is not None:
            old = store.assessments.get(recalculated_from)
            owner = store.conn.execute(
                "SELECT participant_id,inventory_version_id FROM response_sets WHERE id=?",
                (old.response_set_id,),
            ).fetchone()
            if owner[0] != p.id or owner[1] != inventory_version_id:
                raise ValueError(
                    "Yeniden hesaplama aynı katılımcı ve sınav sürümüne ait olmalı"
                )
        # M7 has validated duplicate pairs before this conversion.
        values = (
            dict(responses.items()) if isinstance(responses, dict) else dict(responses)
        )
        rs = store.participants.add_response_set(
            p.id, inventory_version_id, values, source_label=source_label
        )
        assessment = store.assessments.record(
            response_set_id=rs,
            profile_version_id=profile_version_id,
            status=result.status,
            results=result.to_dict(),
            limitations=_limitations(result),
            engine_version=result.engine_version,
            recalculated_from=recalculated_from,
        )
    return {
        "participant_id": p.id,
        "participant_code": p.code,
        "alias": p.alias,
        "assessment_id": assessment.id,
        "response_set_id": rs,
        "result": assessment.results,
    }


def rescore(store, assessment_id, profile_version_id):
    old = store.assessments.get(assessment_id)
    rs = store.conn.execute(
        "SELECT participant_id,inventory_version_id FROM response_sets WHERE id=?",
        (old.response_set_id,),
    ).fetchone()
    p = store.participants.get(rs[0])
    return submit_single(
        store,
        inventory_version_id=rs[1],
        profile_version_id=profile_version_id,
        responses=store.participants.responses(old.response_set_id),
        participant_code=p.code,
        recalculated_from=old.id,
        source_label=f"Yeniden hesaplama #{old.id}",
    )


def job_status(store, job_id, *, include_rows=False):
    row = store.conn.execute(
        "SELECT * FROM assessment_jobs WHERE id=?", (job_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"Toplu iş bulunamadı: {job_id}")
    out = dict(row)
    out["column_mapping"] = json.loads(out["column_mapping"])
    counts = {k: 0 for k in ("succeeded", "partial", "failed", "pending")}
    for state, count in store.conn.execute(
        "SELECT state,count(*) FROM assessment_job_rows WHERE job_id=? GROUP BY state",
        (job_id,),
    ):
        counts[state] = count
    out["counts"] = counts
    out["total"] = sum(counts.values())
    out["done"] = out["total"] - counts["pending"]
    out["progress"] = out["done"] / out["total"] if out["total"] else 1
    out["active_seconds"] = out["preparation_seconds"] + out["processing_seconds"]
    if include_rows:
        out["rows"] = [
            dict(
                r,
                identity=json.loads(r["identity"]),
                errors=json.loads(r["errors"]),
                limitations=json.loads(r["limitations"]) if r["limitations"] else [],
            )
            for r in store.conn.execute(
                "SELECT r.id,r.seq,r.sheet_name,r.row_number,r.identity,r.state,r.assessment_id,r.errors,"
                "a.status AS result_status,json_extract(a.results,'$.full_profile') AS full_profile,a.limitations "
                "FROM assessment_job_rows r LEFT JOIN assessments a ON a.id=r.assessment_id "
                "WHERE r.job_id=? ORDER BY r.seq",
                (job_id,),
            )
        ]
    return out


def list_jobs(store):
    return [
        job_status(store, row[0])
        for row in store.conn.execute("SELECT id FROM assessment_jobs ORDER BY id")
    ]


def create_job(
    store,
    path,
    *,
    inventory_version_id,
    profile_version_id,
    mapping=None,
    sheets=None,
    delimiter=None,
):
    started = time.perf_counter()
    # M7 verifies version compatibility; missing rules remain result limitations.
    evaluate(
        store,
        inventory_version_id=inventory_version_id,
        profile_version_id=profile_version_id,
        responses={},
    )
    items = {
        it["number"]: {c for c, _ in it["options"]}
        for it in store.inventory.items(inventory_version_id)
    }
    source = read_responses(
        Path(path), items, mapping=mapping, sheets=sheets, delimiter=delimiter
    )
    with store.transaction() as c:
        job = c.execute(
            "INSERT INTO assessment_jobs(inventory_version_id,profile_version_id,engine_version,file_name,file_sha256,column_mapping,state) VALUES (?,?,?,?,?,?,?)",
            (
                inventory_version_id,
                profile_version_id,
                ENGINE_VERSION,
                source.file_name,
                source.sha256,
                _json(source.mapping),
                "ready",
            ),
        ).lastrowid
        c.executemany(
            "INSERT INTO assessment_job_rows(job_id,seq,sheet_name,row_number,identity,answers,errors,state) VALUES (?,?,?,?,?,?,?,?)",
            [
                (
                    job,
                    n,
                    r.sheet,
                    r.row,
                    _json(r.identity),
                    _json(r.answers),
                    _json(r.errors),
                    "failed" if r.errors else "pending",
                )
                for n, r in enumerate(source.rows)
            ],
        )
    with store.transaction() as c:
        c.execute(
            "UPDATE assessment_jobs SET preparation_seconds=? WHERE id=?",
            (time.perf_counter() - started, job),
        )
    return job_status(store, job, include_rows=True)


def clone_job(store, job_id, profile_version_id):
    """Aynı donmuş cevaplarla yeni profil için ayrı iş; önceki iş/sonuç değişmez."""
    started = time.perf_counter()
    old = job_status(store, job_id)
    evaluate(
        store,
        inventory_version_id=old["inventory_version_id"],
        profile_version_id=profile_version_id,
        responses={},
    )
    with store.transaction() as c:
        new = c.execute(
            "INSERT INTO assessment_jobs(inventory_version_id,profile_version_id,engine_version,recalculated_from_job_id,file_name,file_sha256,column_mapping,state) VALUES (?,?,?,?,?,?,?,?)",
            (
                old["inventory_version_id"],
                profile_version_id,
                ENGINE_VERSION,
                job_id,
                old["file_name"],
                old["file_sha256"],
                _json(old["column_mapping"]),
                "ready",
            ),
        ).lastrowid
        for row in c.execute(
            "SELECT * FROM assessment_job_rows WHERE job_id=? ORDER BY seq", (job_id,)
        ).fetchall():
            identity = json.loads(row["identity"])
            if row["assessment_id"]:
                previous = store.assessments.get(row["assessment_id"])
                owner = c.execute(
                    "SELECT participant_id FROM response_sets WHERE id=?",
                    (previous.response_set_id,),
                ).fetchone()[0]
                participant = store.participants.get(owner)
                identity["participant_code"] = participant.code
                identity["alias"] = participant.alias or ""
            # Parsing failures persist; runtime failures may be retried in the new job.
            parsing_errors = (
                json.loads(row["errors"])
                if row["state"] == "failed"
                and not any(
                    e.get("stage") == "processing" for e in json.loads(row["errors"])
                )
                else []
            )
            c.execute(
                "INSERT INTO assessment_job_rows(job_id,seq,sheet_name,row_number,identity,answers,errors,state,previous_assessment_id) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    new,
                    row["seq"],
                    row["sheet_name"],
                    row["row_number"],
                    _json(identity),
                    row["answers"],
                    _json(parsing_errors),
                    "failed" if parsing_errors else "pending",
                    row["assessment_id"],
                ),
            )
    with store.transaction() as c:
        c.execute(
            "UPDATE assessment_jobs SET preparation_seconds=? WHERE id=?",
            (time.perf_counter() - started, new),
        )
    return job_status(store, new, include_rows=True)


def control_job(store, job_id, action):
    if action not in ("pause", "cancel"):
        raise ValueError("Geçerli eylemler: pause, cancel")
    with store.transaction() as c:
        state = job_status(store, job_id)["state"]
        if state != "completed":
            c.execute(
                "UPDATE assessment_jobs SET state=? WHERE id=?",
                ("paused" if action == "pause" else "cancelled", job_id),
            )
    return job_status(store, job_id)


def run_job(store, job_id, *, limit=None, progress=None):
    """Her satırda yeniden pending kontrolü + BEGIN IMMEDIATE: iki çalışanda da tek sonuç.

    Dönen çağrı, duraklatılmış/iptal edilmiş iş için açık devam isteğidir. Kesilen bir
    iş running kalmış olsa da pending satırlardan devam eder. Motor sürümü değişirse durur.
    """
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError("Katılımcı sınırı pozitif tamsayı olmalı")
    with store.transaction() as c:
        job = job_status(store, job_id)
        if job["engine_version"] != ENGINE_VERSION:
            raise ValueError(
                "İşin motor sürümü farklı; aynı motorla devam edin veya yeni iş başlatın"
            )
        if job["state"] == "completed":
            return job_status(store, job_id, include_rows=True)
        c.execute("UPDATE assessment_jobs SET state='running' WHERE id=?", (job_id,))
    processed = 0
    try:
        while True:
            began = time.perf_counter()
            with store.transaction() as c:
                job = job_status(store, job_id)
                if job["state"] != "running":
                    break
                row = c.execute(
                    "SELECT * FROM assessment_job_rows WHERE job_id=? AND state='pending' ORDER BY seq LIMIT 1",
                    (job_id,),
                ).fetchone()
                if row is None:
                    c.execute(
                        "UPDATE assessment_jobs SET state='completed' WHERE id=?",
                        (job_id,),
                    )
                    break
                identity = json.loads(row["identity"])
                try:
                    with store.transaction():
                        saved = submit_single(
                            store,
                            inventory_version_id=job["inventory_version_id"],
                            profile_version_id=job["profile_version_id"],
                            responses=json.loads(row["answers"]),
                            source_label=f"{job['file_name']}:{row['sheet_name'] or 'CSV'}:{row['row_number']}",
                            recalculated_from=row["previous_assessment_id"],
                            **identity,
                        )
                        state = (
                            "succeeded"
                            if saved["result"]["status"] == "VERIFIED"
                            else "partial"
                        )
                        c.execute(
                            "UPDATE assessment_job_rows SET state=?,assessment_id=? WHERE id=?",
                            (state, saved["assessment_id"], row["id"]),
                        )
                except (sqlite3.OperationalError, OSError, MemoryError):
                    raise  # Depo/sistem sorunu: katılımcıyı başarısız saymadan duraklat.
                except Exception as e:  # noqa: BLE001 — katılımcı hatası diğer satırları engellemez
                    issue = asdict(
                        Issue(
                            job["file_name"],
                            row["sheet_name"],
                            row["row_number"],
                            identity.get("participant_code") or identity.get("alias"),
                            None,
                            None,
                            f"Değerlendirme kaydedilemedi: {e}",
                            stage="processing",
                        )
                    )
                    c.execute(
                        "UPDATE assessment_job_rows SET state='failed',errors=? WHERE id=?",
                        (_json([issue]), row["id"]),
                    )
                if not c.execute(
                    "SELECT 1 FROM assessment_job_rows WHERE job_id=? AND state='pending' LIMIT 1",
                    (job_id,),
                ).fetchone():
                    c.execute(
                        "UPDATE assessment_jobs SET state='completed' WHERE id=?",
                        (job_id,),
                    )
                c.execute(
                    "UPDATE assessment_jobs SET processing_seconds=processing_seconds+? WHERE id=?",
                    (time.perf_counter() - began, job_id),
                )
            processed += 1
            if progress:
                progress(job_status(store, job_id))
            if limit is not None and processed >= limit:
                control_job(store, job_id, "pause")
                break
    except BaseException:
        # The active row has already rolled back. Previously committed rows stay complete.
        if job_status(store, job_id)["state"] == "running":
            control_job(store, job_id, "pause")
        raise
    return job_status(store, job_id, include_rows=True)
