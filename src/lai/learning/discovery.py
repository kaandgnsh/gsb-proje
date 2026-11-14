"""Puanlama kuralı keşfi: LLM aday önerir, her şey deterministik doğrulanır.

Akış:
 1. Envanter belgelerinin (ve başvuru belgelerinin) ham metni parçalara bölünür; her parça LLM'e
    veri olarak verilir. İstem, ham çıktı ve düşünme metni parça parça saklanır.
 2. Her öneri ayrı ayrı denetlenir: nesne biçimi → katalog → gövde şeması → alıntı belgede
    birebir (yalnız boşluk farkı tolere edilir) → madde/seçenek envanterde → kutup boyutta.
    Reddedilen öneri gerekçesiyle saklanır, bilgi kaydına yazılmaz.
 3. Kabul edilen öneriler kural anahtarına göre birleştirilir: aynı gövde → tek kayıt, tüm
    alıntılar kanıt; farklı gövdeler → CONTRADICTORY (çelişki). Kullanıcı/belge kaynaklı ya da
    CONFIRMED kayıtla çelişen öneri yazılmaz. Yeni kayıtlar WEAKLY_INFERRED başlar.
 4. Örnek uyumu (`fit.run_fit`) durumu deterministik günceller. Hiçbir öneri CONFIRMED olmaz.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

from lai.ai.llm import LLMError, LLMUnavailable
from lai.ai.prompts import DISCOVERY_SYSTEM, OUTPUT_SCHEMA, PROMPT_VERSION, discovery_prompt
from lai.core.rules import RULE_TYPES, canonical, describe_rule, rule_key, validate_rule
from lai.store.enums import Confidence, Source

from .corpus import Corpus
from .fit import FitReport, run_fit

MIN_QUOTE = 8
_NOW = "strftime('%Y-%m-%dT%H:%M:%fZ','now')"
_PROTECTED = (Source.USER, Source.DOCUMENT)


@dataclass
class DiscoveryReport:
    run_id: int
    status: str
    message: str | None = None
    chunk_count: int = 0
    chunk_errors: int = 0
    candidate_count: int = 0
    accepted: int = 0
    rejected: int = 0
    conflicts: int = 0
    new_entries: int = 0
    revised_entries: int = 0
    fit: FitReport | None = None
    rejections: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class _Cand:
    chunk_id: int
    doc_id: int
    seq: int
    raw: object
    rule_type: str | None = None
    body: dict | None = None
    quote: str | None = None
    explanation: str = ""
    key: str | None = None
    status: str = "accepted"
    code: str | None = None
    reason: str | None = None
    hit: object = None
    evidence_id: int | None = None
    entry_id: int | None = None

    def reject(self, code: str, reason: str):
        self.status, self.code, self.reason = "rejected", code, reason


# ------------------------------------------------------------------ yardımcılar
def _inventory_items(store, inventory_id: int) -> dict[int, set[str]] | None:
    row = store.conn.execute("SELECT id FROM inventory_versions WHERE inventory_id=? "
                             "ORDER BY version DESC LIMIT 1", (inventory_id,)).fetchone()
    if row is None:
        return None
    return {it["number"]: {c for c, _ in it["options"]} for it in store.inventory.items(row[0])}


def _known_poles(store, inventory_id: int) -> dict[str, tuple[str, ...]]:
    out = {d["code"]: tuple(d["poles"]) for d in store.inventory.dimensions(inventory_id)
           if d["poles"] and len(d["poles"]) == 2}
    for e in store.knowledge.current(inventory_id, kind="rule"):
        if e.rule_type == "dimension_def" and e.validation != "rejected" and \
                not e.content.get("conflict") and isinstance(e.content.get("body"), dict):
            b = e.content["body"]
            if e.confidence == Confidence.CONFIRMED or b["dimension"] not in out:
                out[b["dimension"]] = tuple(b["poles"])
    return out


def _documents(store, inventory_id: int) -> list[int]:
    return [r[0] for r in store.conn.execute(
        "SELECT d.id FROM source_documents d WHERE d.inventory_id=? AND EXISTS "
        "(SELECT 1 FROM document_raw_units u WHERE u.document_id=d.id) ORDER BY d.id",
        (inventory_id,))]


def _parse_output(text: str):
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict) or set(data) != {"candidates"} or \
            not isinstance(data["candidates"], list):
        return None
    return data["candidates"]


def _norm_len(s: str) -> int:
    return len(re.sub(r"\s+", " ", s).strip())


# ------------------------------------------------------------------ doğrulama
def _check_shape(c: _Cand) -> None:
    r = c.raw
    if not isinstance(r, dict) or not isinstance(r.get("rule_type"), str) or \
            not isinstance(r.get("body"), dict) or not isinstance(r.get("quote"), str):
        c.reject("malformed", "öneri nesnesi 'rule_type' (metin), 'body' (nesne) ve 'quote' "
                              "(metin) alanlarını içermiyor")
        return
    c.rule_type, c.body, c.quote = r["rule_type"], r["body"], r["quote"]
    c.explanation = r.get("explanation") if isinstance(r.get("explanation"), str) else ""
    if c.rule_type not in RULE_TYPES:
        c.reject("unknown_rule_type", f"'{c.rule_type}' kural kataloğunda yok")
        return
    errs = validate_rule(c.rule_type, c.body)
    if errs:
        c.reject("invalid_body", "; ".join(errs[:5]))
        return
    c.key = rule_key(c.rule_type, c.body)


def _check_quote(c: _Cand, store, corpora: dict[int, Corpus], all_docs: list[int]) -> None:
    if _norm_len(c.quote) < MIN_QUOTE:
        c.reject("quote_too_short", f"alıntı çok kısa (en az {MIN_QUOTE} karakter)")
        return
    order = [c.doc_id] + [d for d in all_docs if d != c.doc_id]
    for d in order:
        if d not in corpora:
            corpora[d] = Corpus.for_document(store, d)
        hit = corpora[d].find(c.quote)
        if hit is not None:
            c.hit = hit
            return
    c.reject("quote_not_found", "alıntı belgelerde kelimesi kelimesine bulunamadı "
                                "(yalnız boşluk/satır sonu farkı kabul edilir)")


def _check_inventory(c: _Cand, items: dict[int, set[str]] | None) -> None:
    if items is None:
        return
    b = c.body
    refs = [b["item"]] if "item" in b else list(b.get("items", []))
    missing = [n for n in refs if n not in items]
    if missing:
        c.reject("unknown_item", f"envanterde olmayan madde: {', '.join(map(str, missing[:10]))}")
        return
    codes = set((b.get("options") or b.get("weights") or {}).keys())
    if not codes:
        return
    pool = items[b["item"]] if "item" in b else set().union(*items.values())
    bad = sorted(codes - pool)
    if bad:
        where = f"{b['item']}. maddede" if "item" in b else "hiçbir maddede"
        c.reject("unknown_option", f"{where} olmayan seçenek kodu: {', '.join(bad)}")


def _poles_used(c: _Cand) -> set[str]:
    b, t = c.body, c.rule_type
    if t in ("default_option_map", "item_pole_map"):
        return set(b["options"].values())
    if t == "option_weight":
        return {p for pts in b["weights"].values() for p in pts}
    if t in ("tie_break", "missing_answer_policy") or (t == "threshold" and "pole" in b):
        return {b["pole"]} if "pole" in b else set()
    return set()


def _check_poles(cands: list[_Cand], known: dict[str, tuple[str, ...]]) -> None:
    proposed: dict[str, set] = {}
    for c in cands:
        if c.status == "accepted" and c.rule_type == "dimension_def":
            proposed.setdefault(c.body["dimension"], set()).add(tuple(c.body["poles"]))
    poles = dict(known)
    for d, sets in proposed.items():
        if d not in poles and len(sets) == 1:
            poles[d] = next(iter(sets))
    for c in cands:
        if c.status != "accepted":
            continue
        d = c.body["dimension"]
        if d in poles:
            bad = sorted(_poles_used(c) - set(poles[d]))
            if bad:
                c.reject("unknown_pole", f"{d} boyutunun kutupları {list(poles[d])}; önerideki "
                                         f"{bad} bu boyutta yok")


# ------------------------------------------------------------------ bilgi kaydı
def _evidence(c, store, hit, cache: dict) -> int:
    f = hit.evidence_fields()
    loc = json.dumps(f["locator"], ensure_ascii=False, sort_keys=True)
    k = (f["document_id"], f["page"], f["char_start"], f["char_end"], f["quote"], loc)
    if k not in cache:
        cache[k] = c.execute("INSERT INTO evidence_refs(kind, document_id, page, char_start, "
                             "char_end, quote, locator) VALUES ('document',?,?,?,?,?,?)",
                             (*k[:5], loc)).lastrowid
    return cache[k]


def _same(existing, content: dict, rule_type: str) -> bool:
    ec = existing.content
    if not isinstance(ec.get("body"), dict) or bool(ec.get("conflict")) != bool(content.get("conflict")):
        return False
    if canonical(rule_type, ec["body"]) != canonical(rule_type, content["body"]):
        return False
    alts = lambda x: sorted(canonical(rule_type, a) for a in x.get("alternatives", []))  # noqa: E731
    return alts(ec) == alts(content)


def _write_group(store, inventory_id: int, key: str, group: list[_Cand], rep: DiscoveryReport,
                 creator: str) -> None:
    rt = group[0].rule_type
    bodies: dict[str, dict] = {}
    for c in group:
        bodies.setdefault(canonical(rt, c.body), c.body)
    first = group[0]
    conflict = len(bodies) > 1
    content = {"body": first.body, "explanation": first.explanation}
    if conflict:
        content.update(conflict=True, alternatives=[b for k, b in bodies.items()
                                                    if k != canonical(rt, first.body)])
    ev_ids = list(dict.fromkeys(c.evidence_id for c in group))
    existing = store.knowledge.latest(inventory_id, key)
    k = store.knowledge
    if existing is not None and (existing.confidence == Confidence.CONFIRMED
                                 or existing.source in _PROTECTED):
        for c in group:
            if canonical(rt, c.body) == canonical(rt, existing.content.get("body") or {}):
                c.status, c.entry_id = "accepted", existing.id
            else:
                c.status, c.code = "conflicts_confirmed", "conflicts_confirmed"
                c.reason = (f"kullanıcı/belge kaynaklı kayıtla (#{existing.id}, "
                            f"{existing.confidence}) çelişiyor; yazılmadı")
        return
    if existing is not None and existing.validation == "rejected" and not conflict and \
            canonical(rt, existing.content.get("body") or {}) == canonical(rt, first.body):
        for c in group:
            c.status, c.code = "previously_rejected", "previously_rejected"
            c.reason = f"aynı kural daha önce reddedildi (#{existing.id})"
            c.entry_id = existing.id
        return
    conf = Confidence.CONTRADICTORY if conflict else Confidence.WEAKLY_INFERRED
    reason = ("belgede aynı kural için farklı öneriler var: " +
              " | ".join(describe_rule(rt, b) for b in bodies.values())) if conflict else \
        "LLM önerisi; alıntı belgede doğrulandı"
    if existing is None:
        e = k.record(inventory_id, entry_key=key, kind="rule", rule_type=rt, content=content,
                     summary=describe_rule(rt, first.body), confidence=conf,
                     source=Source.AI_INFERENCE, item_number=first.body.get("item"),
                     dimension_code=first.body["dimension"], evidence_ids=ev_ids, reason=reason,
                     created_by=creator)
        rep.new_entries += 1
    elif _same(existing, content, rt):
        e = existing
    else:
        e = k.revise(existing.id, content=content, summary=describe_rule(rt, first.body),
                     confidence=conf, source=Source.AI_INFERENCE, reason="yeni keşif: " + reason,
                     evidence_ids=ev_ids, created_by=creator)
        rep.revised_entries += 1
    for c in group:
        c.entry_id = e.id
        if conflict:
            c.status = "conflict"


# ------------------------------------------------------------------ ana akış
def _finish(store, rep: DiscoveryReport, status: str, message: str | None, meta: dict) -> None:
    rep.status, rep.message = status, message
    with store.transaction() as c:
        c.execute(f"UPDATE discovery_runs SET status=?, message=?, chunk_count=?, "
                  f"candidate_count=?, accepted_count=?, rejected_count=?, meta=?, "
                  f"finished_at={_NOW} WHERE id=?",
                  (status, message, rep.chunk_count, rep.candidate_count, rep.accepted,
                   rep.rejected, json.dumps(meta, ensure_ascii=False), rep.run_id))


def discover_rules(store, inventory_id: int, llm, *, document_ids: list[int] | None = None,
                   max_chars: int = 6000, progress=None) -> DiscoveryReport:
    all_docs = _documents(store, inventory_id)
    foreign = [d for d in (document_ids or []) if d not in all_docs]
    if foreign:
        raise ValueError(f"belge(ler) {foreign} bu envantere ait değil ya da ham metni yok")
    with store.transaction() as c:
        c.execute(f"UPDATE discovery_runs SET status='failed', finished_at={_NOW}, "
                  f"message='yarıda kalmış çalıştırma (uygulama kapanmış olabilir)' "
                  f"WHERE inventory_id=? AND status='running'", (inventory_id,))
        run_id = c.execute("INSERT INTO discovery_runs(inventory_id, model, prompt_version, think, "
                           "status) VALUES (?,?,?,?, 'running')",
                           (inventory_id, llm.model, PROMPT_VERSION, int(bool(llm.think)))).lastrowid
    rep = DiscoveryReport(run_id, "running")
    docs = list(document_ids) if document_ids else list(all_docs)
    meta = {"documents": docs, "max_chars": max_chars}
    try:
        return _run(store, inventory_id, llm, rep, docs, all_docs, max_chars, meta, progress)
    except BaseException as e:
        if rep.status == "running":
            _finish(store, rep, "failed", f"beklenmeyen hata: {e.__class__.__name__}: {e}", meta)
        raise


def _run(store, inventory_id, llm, rep, docs, all_docs, max_chars, meta, progress):
    items = _inventory_items(store, inventory_id)
    known = _known_poles(store, inventory_id)
    ctx = {"dimensions": known}
    if items:
        ctx.update(item_count=len(items), first=min(items), last=max(items))
    corpora: dict[int, Corpus] = {}
    cands: list[_Cand] = []
    for d in docs:
        corpora[d] = corp = Corpus.for_document(store, d)
        if not corp.text.strip():
            continue
        chunks = corp.chunks(max_chars)
        for ch in chunks:
            prompt = discovery_prompt(ch.text, file_name=corp.file_name, index=ch.index,
                                      total=len(chunks), context=ctx)
            psha = hashlib.sha256((DISCOVERY_SYSTEM + "\n\n" + prompt).encode()).hexdigest()
            row = dict(run_id=rep.run_id, document_id=d, chunk_index=ch.index,
                       char_start=ch.start, char_end=ch.end, prompt_sha256=psha)
            rep.chunk_count += 1
            if progress:
                progress(rep.chunk_count, corp.file_name, ch.index + 1, len(chunks))
            try:
                reply = llm.complete(system=DISCOVERY_SYSTEM, prompt=prompt, schema=OUTPUT_SCHEMA)
            except LLMUnavailable as e:
                _insert_chunk(store, **row, status="llm_error", error=str(e))
                _finish(store, rep, "failed", f"Yerel AI kullanılamıyor: {e}. Bilgi kaydı "
                                              f"yazılmadı.", meta)
                return rep
            except LLMError as e:
                _insert_chunk(store, **row, status="llm_error", error=str(e))
                rep.chunk_errors += 1
                continue
            found = _parse_output(reply.text)
            cid = _insert_chunk(store, **row, status="ok" if found is not None else "invalid_output",
                                raw_output=reply.text, thinking=reply.thinking or None,
                                eval_count=reply.eval_count, duration_ms=reply.duration_ms,
                                error=None if found is not None else
                                "çıktı {\"candidates\": [...]} biçiminde geçerli JSON değil")
            if found is None:
                rep.chunk_errors += 1
                continue
            cands += [_Cand(cid, d, i, raw) for i, raw in enumerate(found)]
    rep.candidate_count = len(cands)
    for c in cands:
        _check_shape(c)
        if c.status == "accepted":
            _check_quote(c, store, corpora, all_docs)
        if c.status == "accepted":
            _check_inventory(c, items)
    _check_poles(cands, known)
    creator = f"keşif:{llm.model}"
    with store.transaction() as tx:
        cache: dict = {}
        groups: dict[str, list[_Cand]] = {}
        for c in cands:
            if c.status == "accepted":
                c.evidence_id = _evidence(tx, store, c.hit, cache)
                groups.setdefault(c.key, []).append(c)
        for key, group in groups.items():
            _write_group(store, inventory_id, key, group, rep, creator)
        for c in cands:
            tx.execute("INSERT INTO discovery_candidates(run_id, chunk_id, seq, raw, rule_type, "
                       "rule_key, body, quote, status, reject_code, reject_reason, evidence_id, "
                       "entry_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (rep.run_id, c.chunk_id, c.seq, json.dumps(c.raw, ensure_ascii=False),
                        c.rule_type, c.key,
                        json.dumps(c.body, ensure_ascii=False) if c.body is not None else None,
                        c.quote, c.status, c.code, c.reason, c.evidence_id, c.entry_id))
    rep.rejected = sum(c.status == "rejected" for c in cands)
    rep.conflicts = sum(c.status == "conflict" for c in cands)
    rep.accepted = sum(c.status in ("accepted", "conflict") for c in cands)
    rep.rejections = [(c.code, c.reason) for c in cands if c.status == "rejected"]
    rep.fit = run_fit(store, inventory_id, discovery_run_id=rep.run_id)
    meta["fit_run_id"] = rep.fit.fit_run_id
    status = "completed_with_errors" if rep.chunk_errors else "completed"
    msg = f"{rep.chunk_errors} parça işlenemedi" if rep.chunk_errors else None
    _finish(store, rep, status, msg, meta)
    return rep


def _insert_chunk(store, **row) -> int:
    with store.transaction() as c:
        cols = ",".join(row)
        return c.execute(f"INSERT INTO discovery_chunks({cols}) VALUES ({','.join('?' * len(row))})",
                         list(row.values())).lastrowid
