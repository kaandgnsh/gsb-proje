"""Testler için sahte LLM'ler (ağ yok, model yok)."""
from __future__ import annotations

import json
import re

from lai.ai.llm import LLMError, LLMReply, LLMUnavailable
from lai.ai.prompts import extract_chunk


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


class ScriptedLLM:
    """`respond(chunk_text, prompt) -> str | dict | Exception` ile yanıt verir; çağrıları kaydeder."""

    def __init__(self, respond, model="sahte-model", think=True):
        self.respond, self.model, self.think = respond, model, think
        self.calls: list[dict] = []

    def complete(self, *, system, prompt, schema):
        chunk = extract_chunk(prompt)
        self.calls.append({"system": system, "prompt": prompt, "schema": schema, "chunk": chunk})
        r = self.respond(chunk, prompt)
        if isinstance(r, Exception):
            raise r
        text = r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)
        return LLMReply(text, "düşünce: sentetik", 7, 1)


def oracle(statements, *, extra=None):
    """İyi bir modeli taklit eder: parçada geçen her yönerge cümlesi için doğru kuralı önerir."""
    def respond(chunk, _prompt):
        n = _norm(chunk)
        cands = [{"rule_type": rt, "body": body, "quote": q, "explanation": "yönergede yazıyor"}
                 for q, rt, body in statements if _norm(q) in n]
        for e in (extra or []):
            if e.get("_when") is None or e["_when"] in n:
                cands.append({k: v for k, v in e.items() if k != "_when"})
        return {"candidates": cands}
    return respond


__all__ = ["ScriptedLLM", "oracle", "LLMError", "LLMUnavailable"]
