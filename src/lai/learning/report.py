"""Keşif ve kural raporları (Türkçe metin + JSON)."""
from __future__ import annotations

from collections import Counter

_STATUS = {"completed": "TAMAMLANDI", "completed_with_errors": "TAMAMLANDI (bazı parçalar "
           "işlenemedi)", "failed": "BAŞARISIZ", "running": "SÜRÜYOR"}
_CONF = {"CONFIRMED": "doğrulandı", "STRONGLY_INFERRED": "güçlü çıkarım",
         "WEAKLY_INFERRED": "zayıf çıkarım", "UNKNOWN": "bilinmiyor",
         "CONTRADICTORY": "çelişkili"}
_REJECT = {"malformed": "biçimsiz öneri", "unknown_rule_type": "katalog dışı kural türü",
           "invalid_body": "şemaya uymayan kural", "quote_too_short": "alıntı çok kısa",
           "quote_not_found": "alıntı belgede yok", "unknown_item": "envanterde olmayan madde",
           "unknown_option": "olmayan seçenek", "unknown_pole": "boyutta olmayan kutup"}


def format_discovery(rep, *, limit: int = 20) -> str:
    lines = [f"Kural keşfi #{rep.run_id}: {_STATUS.get(rep.status, rep.status)}"]
    if rep.message:
        lines.append(rep.message)
    lines.append(f"Parça: {rep.chunk_count} (hatalı {rep.chunk_errors}) · Öneri: "
                 f"{rep.candidate_count} · Kabul: {rep.accepted} · Ret: {rep.rejected} · "
                 f"Çelişki: {rep.conflicts}")
    lines.append(f"Bilgi kaydı: {rep.new_entries} yeni, {rep.revised_entries} güncellendi")
    if rep.fit is not None:
        oc = Counter(rep.fit.outcomes.values())
        lines.append(f"Örnek uyumu ({rep.fit.example_count} örnek): {oc['consistent']} tutarlı, "
                     f"{oc['inconsistent']} çelişkili, {oc['not_testable']} sınanamadı, "
                     f"{oc['conflict']} belgede çelişen")
    if rep.rejections:
        lines.append(f"Reddedilen öneriler ({len(rep.rejections)}):")
        for code, reason in rep.rejections[:limit]:
            lines.append(f"  ✗ [{_REJECT.get(code, code)}] {reason}")
        if len(rep.rejections) > limit:
            lines.append(f"  … {len(rep.rejections) - limit} tane daha")
    lines.append("Not: Hiçbir öneri kendiliğinden doğrulanmış sayılmaz; kurallar kullanıcı "
                 "onayıyla CONFIRMED olur.")
    return "\n".join(lines)


def discovery_dict(rep) -> dict:
    return {"run_id": rep.run_id, "status": rep.status, "message": rep.message,
            "chunks": rep.chunk_count, "chunk_errors": rep.chunk_errors,
            "candidates": rep.candidate_count, "accepted": rep.accepted,
            "rejected": rep.rejected, "conflicts": rep.conflicts, "new_entries": rep.new_entries,
            "revised_entries": rep.revised_entries,
            "fit": None if rep.fit is None else {"fit_run_id": rep.fit.fit_run_id,
                                                 "examples": rep.fit.example_count,
                                                 "outcomes": rep.fit.outcomes},
            "rejections": [{"code": c, "reason": r} for c, r in rep.rejections]}


def rules_rows(store, inventory_id: int) -> list[dict]:
    return [{"key": e.entry_key, "version": e.version, "rule_type": e.rule_type,
             "summary": e.summary, "confidence": e.confidence, "source": e.source,
             "validation": e.validation, "evidence": len(e.evidence_ids), "reason": e.reason}
            for e in store.knowledge.current(inventory_id, kind="rule")]


def format_rules(rows: list[dict]) -> str:
    if not rows:
        return "Kayıtlı kural yok."
    out = []
    for r in rows:
        out.append(f"[{_CONF.get(r['confidence'], r['confidence'])}] {r['summary']}  "
                   f"(v{r['version']}, kaynak {r['source']}, {r['evidence']} kanıt)")
        if r["reason"]:
            out.append(f"    {r['reason']}")
    c = Counter(r["confidence"] for r in rows)
    out.append("Toplam: " + ", ".join(f"{_CONF.get(k, k)} {v}" for k, v in sorted(c.items())))
    return "\n".join(out)
