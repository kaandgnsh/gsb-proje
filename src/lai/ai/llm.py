"""LLM soyutlaması. Keşif kodu yalnız bu protokolü görür; testler sahte LLM kullanır.

`OllamaLLM` yalnız loopback adresine bağlanır (lai_runtime.client denetler), sıcaklık 0 ve sabit
tohum ile çalışır. Şemaya uymayan çıktı hata değildir: ham metin döndürülür, doğrulamayı keşif
katmanı aday aday yapar (tek bozuk öneri tüm parçayı düşürmez).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from lai_runtime.client import (ModelNotFoundError, OllamaClient, OllamaError,
                                RuntimeUnavailableError, StructuredOutputError)


class LLMUnavailable(RuntimeError):
    """Model ya da çalışma ortamı yok: çalıştırmanın devamı anlamsız."""


class LLMError(RuntimeError):
    """Tek bir çağrı başarısız (ör. HTTP 500): yalnız o parça etkilenir."""


@dataclass(frozen=True)
class LLMReply:
    text: str
    thinking: str
    eval_count: int
    duration_ms: int


class LLM(Protocol):
    model: str
    think: bool

    def complete(self, *, system: str, prompt: str, schema: dict) -> LLMReply: ...


class OllamaLLM:
    # num_ctx açıkça verilir: varsayılan bağlam penceresi küçük olabilir ve aşılırsa Ollama
    # istemin BAŞINI (sistem talimatları dahil) sessizce keser. num_predict düşünme + yanıtı
    # sınırlar. Parça boyutu (6000 karakter) bu bütçenin yarısına sığacak şekilde seçildi.
    OPTIONS = {"temperature": 0, "seed": 0, "num_ctx": 12288, "num_predict": 6144}

    def __init__(self, base_url: str, model: str, *, think: bool = True, timeout: float = 900,
                 keep_alive: str | None = None):
        self.client = OllamaClient(base_url, timeout=timeout)
        self.model, self.think, self.keep_alive = model, think, keep_alive

    def complete(self, *, system: str, prompt: str, schema: dict) -> LLMReply:
        try:
            r = self.client.generate(self.model, prompt, system=system, schema=schema,
                                     options=dict(self.OPTIONS), think=self.think,
                                     keep_alive=self.keep_alive)
        except StructuredOutputError as e:
            return LLMReply(e.raw, "", 0, 0)
        except (RuntimeUnavailableError, ModelNotFoundError) as e:
            raise LLMUnavailable(str(e)) from None
        except OllamaError as e:
            raise LLMError(str(e)) from None
        return LLMReply(r.text, r.thinking, r.eval_count, r.total_duration_ns // 1_000_000)
