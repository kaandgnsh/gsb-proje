"""Belgeyi veritabanına aktarır.

Kurallar:
- Ham birimler (`document_raw_units`) ile ayrıştırılmış kayıtlar (`parsed_*`) ayrı saklanır.
- Hata (error) varsa envanter sürümü OLUŞTURULMAZ (`needs_review`); ayrıştırılmış kayıtlar ve
  sorunlar yine saklanır.
- Aynı envanterde aynı sha256 + aynı ayrıştırıcı sürümü + aynı seçenekler → `duplicate`;
  yeni veri yazılmaz. Başarısız (`failed`) çalıştırmalar yeniden denemeyi engellemez.
- İçerik son envanter sürümüyle aynıysa yeni sürüm açılmaz.
- Her madde ve seçenek, belge + konum + alıntı içeren bir kanıt kaydına bağlanır.
- Özgün dosya, içerik adresli (sha256) salt okunur bir kopya olarak saklanır.
- Tüm veritabanı yazımı tek işlemdir; yarıda hata → hiçbir şey yazılmaz.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .detect import MAX_BYTES, detect_kind, sha256_file
from .model import ERROR, INFO, PARSER_VERSION, WARNING, IngestError, Issue, ParseResult
from .parse import parse_file


@dataclass
class ImportReport:
    status: str
    run_id: int
    file_name: str
    sha256: str
    kind: str
    document_id: int | None = None
    inventory_version_id: int | None = None
    inventory_version: int | None = None
    duplicate_of_run_id: int | None = None
    same_content: bool = False
    item_count: int = 0
    option_count: int = 0
    answer_key_count: int = 0
    message: str | None = None
    issues: list[Issue] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def _sev(self, s):
        return [i for i in self.issues if i.severity == s]

    @property
    def errors(self):
        return self._sev(ERROR)

    @property
    def warnings(self):
        return self._sev(WARNING)

    @property
    def infos(self):
        return self._sev(INFO)


def _loc(loc) -> str | None:
    return json.dumps(loc.to_dict(), ensure_ascii=False) if loc is not None else None


def content_hash(res: ParseResult) -> str:
    payload = [[it.number, it.text, [[o.code, o.text] for o in it.options]] for it in res.items]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                          .encode("utf-8")).hexdigest()


def _store_copy(src: Path, sha: str, kind: str, documents_dir: Path) -> Path:
    dest = Path(documents_dir) / sha[:2] / f"{sha}.{kind}"
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    shutil.copyfile(src, tmp)
    if sha256_file(tmp)[0] != sha:
        tmp.unlink()
        raise IngestError("copy_mismatch", f"{src.name}: kopyalanan dosya özgünüyle aynı değil "
                                           f"(dosya okuma sırasında değişmiş olabilir).")
    os.chmod(tmp, 0o400)
    os.replace(tmp, dest)
    return dest


def _insert_run(c, **row) -> int:
    cols = ",".join(row)
    return c.execute(f"INSERT INTO import_runs({cols}) VALUES ({','.join('?' * len(row))})",
                     list(row.values())).lastrowid


def _insert_parsed(c, run_id: int, res: ParseResult) -> None:
    for it in res.items:
        pid = c.execute("INSERT INTO parsed_items(import_run_id, seq, number, label, text, locator, "
                        "raw_first, raw_last) VALUES (?,?,?,?,?,?,?,?)",
                        (run_id, it.seq, it.number, it.label, it.text, _loc(it.locator),
                         it.raw_first, it.raw_last)).lastrowid
        c.executemany("INSERT INTO parsed_options(parsed_item_id, seq, code, text, locator) "
                      "VALUES (?,?,?,?,?)",
                      [(pid, j, o.code, o.text, _loc(o.locator)) for j, o in enumerate(it.options)])
    c.executemany("INSERT INTO parsed_answer_keys(import_run_id, seq, item_number, value, locator) "
                  "VALUES (?,?,?,?,?)",
                  [(run_id, k.seq, k.item_number, k.value, _loc(k.locator)) for k in res.answer_keys])


def _insert_issues(c, run_id: int, issues: list[Issue]) -> None:
    c.executemany("INSERT INTO import_issues(import_run_id, severity, code, message, locator, "
                  "item_seq, item_number) VALUES (?,?,?,?,?,?,?)",
                  [(run_id, i.severity, i.code, i.message, _loc(i.locator), i.item_seq,
                    i.item_number) for i in issues])


def _evidence(c, document_id: int, loc, quote: str) -> int:
    return c.execute("INSERT INTO evidence_refs(kind, document_id, page, char_start, char_end, "
                     "quote, locator) VALUES ('document',?,?,?,?,?,?)",
                     (document_id, loc.page, loc.char_start, loc.char_end, quote,
                      _loc(loc))).lastrowid


def _publish_items(c, version_id: int, document_id: int, res: ParseResult) -> None:
    for it in res.items:
        ev = _evidence(c, document_id, it.locator, it.text)
        iid = c.execute("INSERT INTO items(inventory_version_id, number, text, evidence_id) "
                        "VALUES (?,?,?,?)", (version_id, it.number, it.text, ev)).lastrowid
        for pos, o in enumerate(it.options):
            oev = _evidence(c, document_id, o.locator, o.text)
            c.execute("INSERT INTO options(item_id, code, text, position, evidence_id) "
                      "VALUES (?,?,?,?,?)", (iid, o.code, o.text, pos, oev))


def _raw_units(c, document_id: int, units) -> None:
    if c.execute("SELECT 1 FROM document_raw_units WHERE document_id=? LIMIT 1",
                 (document_id,)).fetchone():
        return  # aynı belgenin ham içeriği zaten saklı (ör. önceki başarısız denemeden)
    c.executemany("INSERT INTO document_raw_units(document_id, idx, page, text, locator) "
                  "VALUES (?,?,?,?,?)",
                  [(document_id, u.index, u.locator.page, u.text, _loc(u.locator))
                   for u in units])


def import_document(store, inventory_id: int, path: Path, *, documents_dir: Path,
                    kind: str | None = None, encoding: str | None = None,
                    max_bytes: int = MAX_BYTES, label: str | None = None) -> ImportReport:
    path = Path(path)
    if not path.is_file():
        raise IngestError("not_found", f"{path}: dosya bulunamadı.")
    sha, size = sha256_file(path)
    options = {"kind": kind, "encoding": encoding}
    declared = kind or path.suffix.lower().lstrip(".") or "?"
    conn = store.conn

    prev = conn.execute(
        "SELECT * FROM import_runs WHERE inventory_id=? AND sha256=? AND parser_version=? "
        "AND status IN ('imported','needs_review') AND json_extract(meta,'$.options')=json(?) "
        "ORDER BY id LIMIT 1",
        (inventory_id, sha, PARSER_VERSION, json.dumps(options, sort_keys=True))).fetchone()
    if prev is not None:
        with store.transaction() as c:
            rid = _insert_run(c, inventory_id=inventory_id, document_id=prev["document_id"],
                              file_name=path.name, sha256=sha, kind=prev["kind"],
                              parser_version=PARSER_VERSION, status="duplicate",
                              duplicate_of_run_id=prev["id"],
                              inventory_version_id=prev["inventory_version_id"],
                              message="aynı belge daha önce içe aktarıldı",
                              meta=json.dumps({"options": options}, sort_keys=True))
        return ImportReport("duplicate", rid, path.name, sha, prev["kind"],
                            document_id=prev["document_id"],
                            inventory_version_id=prev["inventory_version_id"],
                            duplicate_of_run_id=prev["id"],
                            message=f"Bu belge daha önce içe aktarıldı (çalıştırma #{prev['id']}, "
                                    f"durum: {prev['status']}). Yeni kayıt oluşturulmadı.")

    res: ParseResult | None = None
    failure: IngestError | None = None
    try:
        res = parse_file(path, kind=kind, encoding=encoding, max_bytes=max_bytes)
    except IngestError as e:
        failure = e
    real_kind = res.kind if res else declared
    stored = _store_copy(path, sha, real_kind, documents_dir) if size <= max_bytes else None

    with store.transaction() as c:
        doc_id, _ = store.inventory.add_document(inventory_id, file_name=path.name, sha256=sha,
                                                 kind=real_kind, size_bytes=size,
                                                 stored_path=str(stored) if stored else None)
        meta = {"options": options}
        if failure is not None:
            issue = Issue(ERROR, failure.code, failure.message)
            rid = _insert_run(c, inventory_id=inventory_id, document_id=doc_id, file_name=path.name,
                              sha256=sha, kind=real_kind, parser_version=PARSER_VERSION,
                              status="failed", error_count=1, message=failure.message,
                              meta=json.dumps(meta, sort_keys=True))
            _insert_issues(c, rid, [issue])
            return ImportReport("failed", rid, path.name, sha, real_kind, document_id=doc_id,
                                message=failure.message, issues=[issue])

        _raw_units(c, doc_id, res.raw_units)
        n_opts = sum(len(it.options) for it in res.items)
        chash = content_hash(res)
        version_id = version_no = None
        same = False
        message = None
        if not res.has_errors:
            last = c.execute("SELECT id, version, content_hash FROM inventory_versions "
                             "WHERE inventory_id=? ORDER BY version DESC LIMIT 1",
                             (inventory_id,)).fetchone()
            if last is not None and last["content_hash"] == chash:
                version_id, version_no, same = last["id"], last["version"], True
                message = (f"İçerik mevcut envanter sürümü v{version_no} ile aynı; yeni sürüm "
                           f"oluşturulmadı.")
            else:
                version_no = (last["version"] + 1) if last else 1
                version_id = c.execute(
                    "INSERT INTO inventory_versions(inventory_id, version, label, content_hash) "
                    "VALUES (?,?,?,?)", (inventory_id, version_no, label or path.name,
                                         chash)).lastrowid
        status = "needs_review" if res.has_errors else "imported"
        meta.update(content_hash=chash, **{k: v for k, v in res.meta.items()
                                           if isinstance(v, (int, str, list, dict))})
        rid = _insert_run(c, inventory_id=inventory_id, document_id=doc_id, file_name=path.name,
                          sha256=sha, kind=real_kind, parser_version=PARSER_VERSION, status=status,
                          inventory_version_id=version_id, item_count=len(res.items),
                          option_count=n_opts, answer_key_count=len(res.answer_keys),
                          error_count=res.count(ERROR), warning_count=res.count(WARNING),
                          message=message, meta=json.dumps(meta, ensure_ascii=False, sort_keys=True))
        _insert_parsed(c, rid, res)
        _insert_issues(c, rid, res.issues)
        if version_id is not None and not same:
            _publish_items(c, version_id, doc_id, res)
            c.execute("UPDATE inventory_versions SET source_run_id=? WHERE id=?", (rid, version_id))
    if status == "needs_review":
        message = (f"{res.count(ERROR)} hata nedeniyle envanter sürümü oluşturulmadı. Ayrıştırılan "
                   f"kayıtlar ve sorunlar incelenmek üzere saklandı.")
    return ImportReport(status, rid, path.name, sha, real_kind, document_id=doc_id,
                        inventory_version_id=version_id, inventory_version=version_no,
                        same_content=same, item_count=len(res.items), option_count=n_opts,
                        answer_key_count=len(res.answer_keys), message=message,
                        issues=list(res.issues), meta=res.meta)


@dataclass
class ReferenceImport:
    document_id: int
    file_name: str
    sha256: str
    kind: str
    unit_count: int
    duplicate: bool
    issues: list[Issue] = field(default_factory=list)


def import_reference_document(store, inventory_id: int, path: Path, *, documents_dir: Path,
                              kind: str | None = None, encoding: str | None = None,
                              max_bytes: int = MAX_BYTES) -> ReferenceImport:
    """Madde içermeyen başvuru belgesini (ör. puanlama yönergesi) ham metin olarak saklar.

    Ayrıştırma ve yorum yapılmaz; metin birebir `document_raw_units`'e yazılır. Aynı belge
    ikinci kez kaydedilmez. Belge daha önce envanter belgesi olarak içe aktarıldıysa ham
    birimleri yeniden yazılmaz, yalnız başvuru olarak işaretlenir."""
    from .readers import read
    path = Path(path)
    if not path.is_file():
        raise IngestError("not_found", f"{path}: dosya bulunamadı.")
    real_kind = detect_kind(path, declared=kind, max_bytes=max_bytes)
    sha, size = sha256_file(path)
    row = store.conn.execute(
        "SELECT r.document_id, r.unit_count, d.kind FROM reference_documents r "
        "JOIN source_documents d ON d.id = r.document_id "
        "WHERE r.inventory_id=? AND d.sha256=?", (inventory_id, sha)).fetchone()
    if row is not None:
        return ReferenceImport(row[0], path.name, sha, row[2], row[1], True)
    out = read(path, real_kind, encoding=encoding)
    if not any(u.text.strip() for u in out.units):
        raise IngestError("no_text", f"{path.name}: belgede okunabilir metin yok (taranmış "
                                     f"görüntü olabilir; OCR desteklenmiyor).")
    stored = _store_copy(path, sha, real_kind, documents_dir)
    with store.transaction() as c:
        doc_id, _ = store.inventory.add_document(inventory_id, file_name=path.name, sha256=sha,
                                                 kind=real_kind, size_bytes=size,
                                                 stored_path=str(stored))
        _raw_units(c, doc_id, out.units)
        n = c.execute("SELECT COUNT(*) FROM document_raw_units WHERE document_id=?",
                      (doc_id,)).fetchone()[0]
        c.execute("INSERT INTO reference_documents(document_id, inventory_id, unit_count) "
                  "VALUES (?,?,?)", (doc_id, inventory_id, n))
    return ReferenceImport(doc_id, path.name, sha, real_kind, n, False, list(out.issues))
