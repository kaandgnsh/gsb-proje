"""Qt'siz masaüstü köprüsü: CLI ile aynı servisler, her çağrıda ayrı Store."""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

from lai.assessment.files import read_responses
from lai.assessment.service import (
    control_job,
    create_job,
    job_status,
    list_jobs,
    rescore,
    run_job,
    submit_single,
)
from lai.ingest.answer_key import import_answer_key, parse_answer_key
from lai.ingest.detect import detect_kind, sha256_file
from lai.ingest.parse import parse_file
from lai.ingest.readers import read
from lai.ingest.service import import_document, import_reference_document
from lai.learning.discovery import discover_rules
from lai.learning.interactive import analyze, answer, gaps, interpret_answer
from lai.learning.report import discovery_dict
from lai.store.store import Store


class Cancelled(RuntimeError):
    pass


class Backend:
    def __init__(self, db, *, llm_factory=None):
        self.db = Path(db)
        self.llm_factory = llm_factory
        self._runtime = None

    def shutdown(self):
        if self._runtime is not None:
            self._runtime.stop()

    def start_runtime(self, args):
        from lai_runtime.config import RuntimeConfig
        from lai_runtime.process import RuntimeManager
        from lai_runtime.sandbox import require_isolation
        from lai_setup.paths import resolve_paths

        require_isolation()
        cfg = RuntimeConfig(
            port=args.get("port", 11435), model=args.get("model", "qwen3.5:4b")
        )
        if self._runtime is not None and self._runtime.cfg != cfg:
            if not self._runtime.stop() and self._runtime.status().running:
                raise RuntimeError(
                    "Önceki uygulama süreci kapatılamadı; yeni süreç başlatılmadı"
                )
            self._runtime = None
        if self._runtime is None:
            paths = resolve_paths("linux", os.environ, Path.home())
            paths.runtime.mkdir(parents=True, exist_ok=True)
            self._runtime = RuntimeManager(cfg, paths)
        return self._runtime.start()

    def _llm(self, args):
        if self.llm_factory:
            return self.llm_factory()
        if os.environ.get("LAI_SANDBOX_ACTIVE"):
            self.start_runtime(args)
        from lai.ai.llm import OllamaLLM
        from lai_runtime.config import RuntimeConfig

        cfg = RuntimeConfig(
            port=args.get("port", 11435), model=args.get("model", "qwen3.5:4b")
        )
        return OllamaLLM(cfg.base_url, cfg.model, timeout=30)

    def execute(self, operation, args, cancel, progress):
        def check():
            if cancel.is_set():
                raise Cancelled("İşlem iptal edildi; tamamlanmış kayıtlar korunuyor.")

        check()
        args = dict(args)
        history_offset = args.pop("_history_offset", 0)
        with _opened(self.db) as store:
            result = self._perform(store, operation, args, cancel, progress, check)
            if isinstance(result, dict):
                result["catalog"] = self.catalog(store, history_offset)
            return result

    def catalog(self, s, history_offset=0):
        return {
            "inventories": [
                dict(r)
                for r in s.conn.execute("SELECT id,name FROM inventories ORDER BY id")
            ],
            "versions": [
                dict(r)
                for r in s.conn.execute(
                    "SELECT id,inventory_id,version,label FROM inventory_versions ORDER BY id"
                )
            ],
            "profiles": [
                {
                    "id": r["id"],
                    "inventory_id": r["inventory_id"],
                    "version": r["version"],
                    "provisional": bool(r["provisional"]),
                    "inventory_version_id": json.loads(r["snapshot"]).get(
                        "inventory_version_id"
                    ),
                }
                for r in s.conn.execute(
                    "SELECT v.*,p.inventory_id FROM scoring_profile_versions v JOIN scoring_profiles p ON p.id=v.profile_id ORDER BY v.id"
                )
            ],
            "jobs": list_jobs(s),
            "history_total": s.conn.execute(
                "SELECT count(*) FROM assessments"
            ).fetchone()[0],
            "history": [
                dict(r)
                for r in s.conn.execute(
                    "SELECT a.id,a.status,a.profile_version_id,a.created_at,r.inventory_version_id,v.inventory_id,p.code,p.alias FROM assessments a JOIN response_sets r ON r.id=a.response_set_id JOIN inventory_versions v ON v.id=r.inventory_version_id JOIN participants p ON p.id=r.participant_id ORDER BY a.id DESC LIMIT 500 OFFSET ?",
                    (history_offset,),
                )
            ],
            "participants": [asdict(p) for p in s.participants.list()],
        }

    def _perform(self, s, op, a, cancel, progress, check):
        if op == "runtime_start":
            result = self.start_runtime(a)
            return {
                "runtime_message": f"Uygulamaya ait Ollama başladı: pid {result.pid}, port {result.port}. Ağ yalıtımı etkin."
            }
        if op == "runtime_stop":
            self.shutdown()
            return {"runtime_message": "Yalnız uygulamaya ait süreç durduruldu."}
        if op == "preview_report":
            path = Path(a["path"])
            if path.suffix.lower() == ".pdf":
                import pymupdf

                with pymupdf.open(path) as doc:
                    pages = len(doc)
                return {
                    "preview_report": {"path": str(path), "kind": "pdf", "pages": pages}
                }
            if path.suffix.lower() == ".xlsx":
                from openpyxl import load_workbook

                wb = load_workbook(
                    path, read_only=True, data_only=False, keep_links=False
                )
                try:
                    sheets = []
                    for sheet in wb:
                        data = []
                        for n, row in enumerate(sheet.iter_rows(values_only=True)):
                            check()
                            if n >= 500:
                                break
                            data.append([str(v) if v is not None else "" for v in row])
                        sheets.append({"name": sheet.title, "rows": data})
                    return {
                        "preview_report": {
                            "path": str(path),
                            "kind": "xlsx",
                            "sheets": sheets,
                        }
                    }
                finally:
                    wb.close()
            raise ValueError("Uygulama içi önizleme PDF ve XLSX için kullanılabilir")
        if op in ("export_assessment", "export_job"):
            from lai.reports.service import export_assessment, export_job

            fn = export_assessment if op == "export_assessment" else export_job
            return {
                "exported": fn(
                    s,
                    a["id"],
                    a["target"],
                    a["format"],
                    cancel=cancel.is_set,
                    progress=progress,
                )
            }
        if op == "report_commentary":
            from lai.reports.commentary import generate

            body = generate(s, a["id"], self._llm(a))
            return {"commentary": dict(body, assessment_id=a["id"])}
        if op == "refresh":
            return {}
        if op == "create_inventory":
            name = a["name"].strip()
            if not name or len(name) > 128:
                raise ValueError("Envanter adı 1–128 karakter olmalı.")
            if s.conn.execute(
                "SELECT 1 FROM inventories WHERE name=?", (name,)
            ).fetchone():
                raise ValueError("Bu adla envanter var; listeden seçin.")
            return {"inventory_id": s.inventory.create_inventory(name)}
        if op == "preview_document":
            path = Path(a["path"])
            role = a["role"]
            sha, _ = sha256_file(path)
            progress({"message": "Belge okunuyor ve kayıtlar doğrulanıyor…"})
            if role == "inventory":
                parsed = parse_file(path)
            elif role == "answer_key":
                version = s.conn.execute(
                    "SELECT id FROM inventory_versions WHERE inventory_id=? ORDER BY version DESC LIMIT 1",
                    (a["inventory_id"],),
                ).fetchone()
                parsed = parse_answer_key(
                    path,
                    known_items={i["number"] for i in s.inventory.items(version[0])}
                    if version
                    else None,
                )
            else:
                kind = detect_kind(path)
                out = read(path, kind)
                from lai.ingest.model import ParseResult

                parsed = ParseResult(kind, raw_units=out.units, issues=out.issues)
                if not any(u.text.strip() for u in out.units):
                    raise ValueError(
                        "Belgede okunabilir metin yok; OCR desteklenmiyor."
                    )
            check()
            return {
                "preview": {
                    "path": str(path),
                    "role": role,
                    "sha256": sha,
                    "inventory_id": a["inventory_id"],
                    "questions": len(parsed.items),
                    "answer_keys": len(parsed.answer_keys),
                    "units": len(parsed.raw_units),
                    "issues": [
                        {
                            "severity": i.severity,
                            "message": i.message,
                            "location": i.locator.describe() if i.locator else "",
                            "item": i.item_number,
                        }
                        for i in parsed.issues
                    ],
                    "sample": (
                        [
                            {
                                "number": k.item_number,
                                "text": k.value,
                                "location": k.locator.describe(),
                            }
                            for k in parsed.answer_keys[:100]
                        ]
                        if role == "answer_key"
                        else [
                            {
                                "number": i.number,
                                "text": i.text,
                                "location": i.locator.describe(),
                            }
                            for i in parsed.items[:100]
                        ]
                    ),
                    "text_sample": "\n".join(u.text for u in parsed.raw_units[:15]),
                }
            }
        if op == "save_document":
            p = a["preview"]
            path = Path(p["path"])
            if sha256_file(path)[0] != p["sha256"]:
                raise ValueError("Dosya önizlemeden sonra değişti; yeniden önizleyin.")
            if any(i["severity"] == "error" for i in p["issues"]):
                raise ValueError("Belgede hata var; kayıt yapılmadı.")
            check()
            directory = self.db.parent / "documents"
            if p["role"] == "inventory":
                r = asdict(
                    import_document(s, p["inventory_id"], path, documents_dir=directory)
                )
            elif p["role"] == "answer_key":
                r = import_answer_key(
                    s, p["inventory_id"], path, documents_dir=directory
                )
            else:
                r = asdict(
                    import_reference_document(
                        s, p["inventory_id"], path, documents_dir=directory
                    )
                )
            return {"imported": r}
        if op == "learning":
            progress({"message": "Eksikler ve kaynak kuralları inceleniyor…"})
            analyze(s, a["inventory_id"])
            check()
            entries = s.knowledge.current(a["inventory_id"])
            return {
                "gaps": [
                    asdict(g) for g in gaps(s, a["inventory_id"], include_closed=True)
                ],
                "rules": [asdict(e) for e in entries],
                "answer_key_sources": [
                    dict(r)
                    for r in s.conn.execute(
                        "SELECT d.file_name,k.item_number,k.value,k.locator FROM parsed_answer_keys k JOIN import_runs r ON r.id=k.import_run_id JOIN source_documents d ON d.id=r.document_id WHERE r.inventory_id=?",
                        (a["inventory_id"],),
                    )
                ],
                "evidence": [
                    dict(r)
                    for r in s.conn.execute(
                        "SELECT e.*,d.file_name FROM evidence_refs e LEFT JOIN source_documents d ON d.id=e.document_id WHERE e.id IN (SELECT ke.evidence_id FROM knowledge_evidence ke JOIN knowledge_entries k ON k.id=ke.entry_id WHERE k.inventory_id=?)",
                        (a["inventory_id"],),
                    )
                ],
            }
        if op == "answer":
            return {
                "answer": answer(
                    s,
                    a["gap_id"],
                    a["action"],
                    a.get("text", ""),
                    structured=a.get("structured"),
                )
            }
        if op == "interpret":
            return {"answer": interpret_answer(s, a["gap_id"], a["text"], self._llm(a))}
        if op == "discover":

            def update(n, name, i, total):
                check()
                progress(
                    {
                        "message": f"{name}: parça {i}/{total}",
                        "value": int(100 * i / max(total, 1)),
                    }
                )

            return {
                "discovery": discovery_dict(
                    discover_rules(s, a["inventory_id"], self._llm(a), progress=update)
                )
            }
        if op == "publish":
            pid = s.profiles.ensure_profile(a["inventory_id"])
            return {"profile": asdict(s.profiles.publish_version(pid))}
        if op == "items":
            return {
                "items": s.inventory.items(a["version_id"]),
                "version_id": a["version_id"],
            }
        if op == "single":
            return submit_single(s, **a)
        if op == "result":
            return {"assessment": asdict(s.assessments.get(a["assessment_id"]))}
        if op == "rescore":
            return rescore(s, a["assessment_id"], a["profile_version_id"])
        if op == "preview_batch":
            if a.get("mapping_path"):
                from lai.assessment.cli import _read_json

                a = dict(a, mapping=_read_json(a["mapping_path"]))
            from lai.scoring import evaluate

            evaluate(
                s,
                inventory_version_id=a["inventory_version_id"],
                profile_version_id=a["profile_version_id"],
                responses={},
            )
            progress({"message": "Cevap dosyası ve sütunlar doğrulanıyor…"})
            items = {
                i["number"]: {c for c, _ in i["options"]}
                for i in s.inventory.items(a["inventory_version_id"])
            }
            src = read_responses(
                Path(a["path"]), items, mapping=a.get("mapping"), sheets=a.get("sheets")
            )
            check()
            return {
                "batch_preview": {
                    "args": a,
                    "sha256": src.sha256,
                    "mapping": src.mapping,
                    "total": len(src.rows),
                    "failed": sum(bool(r.errors) for r in src.rows),
                    "rows": [asdict(r) for r in src.rows[:500]],
                    "issues": [e for r in src.rows for e in r.errors][:1000],
                }
            }
        if op == "prepare_job":
            p = a["preview"]
            args = p["args"]
            if sha256_file(Path(args["path"]))[0] != p["sha256"]:
                raise ValueError("Cevap dosyası değişti; yeniden önizleyin.")
            check()
            return {
                "job": create_job(
                    s,
                    args["path"],
                    inventory_version_id=args["inventory_version_id"],
                    profile_version_id=args["profile_version_id"],
                    mapping=args.get("mapping"),
                    sheets=args.get("sheets"),
                )
            }
        if op == "job":
            return {"job": job_status(s, a["job_id"], include_rows=True)}
        if op == "run_job":

            def checkpoint(info):
                progress(
                    {
                        "job": info,
                        "message": f"{info['done']}/{info['total']} katılımcı kaydedildi",
                        "value": int(info["progress"] * 100),
                    }
                )
                if cancel.is_set():
                    control_job(s, a["job_id"], a.get("cancel_action", "pause"))

            if cancel.is_set():
                return {"job": control_job(s, a["job_id"], "pause")}
            return {"job": run_job(s, a["job_id"], progress=checkpoint)}
        if op == "control_job":
            return {"job": control_job(s, a["job_id"], a["action"])}
        if op == "model_status":
            from lai.ai.status import model_status

            return {"model_status": model_status(**a)}
        raise ValueError("Bilinmeyen masaüstü işlemi.")


from contextlib import contextmanager


@contextmanager
def _opened(db):
    s = Store.open(db, backup_dir=Path(db).parent / "backups")
    try:
        yield s
    finally:
        s.close()
