"""Yalnız açık istekle uygulamanın loopback modelini salt okunur kontrol et."""

from lai_runtime.client import OllamaClient, OllamaError
from lai_runtime.config import RuntimeConfig


def model_status(port=11435, model="qwen3.5:4b"):
    cfg = RuntimeConfig(port=port, model=model)
    try:
        data = OllamaClient(cfg.base_url, timeout=2).list_models()
        names = [m.get("name", m.get("model", "")) for m in data if isinstance(m, dict)]
        available = model in names
        return {
            "available": available,
            "message": f"{model}: hazır."
            if available
            else f"{model} yerel serviste bulunamadı. Belge aktarımı ve hazır profille puanlama çalışır.",
            "models": names,
        }
    except (OllamaError, ValueError, TypeError, AttributeError):
        return {
            "available": False,
            "message": "Yerel model servisine ulaşılamadı. Belge aktarımı, manuel öğrenme ve hazır profille puanlama kullanılabilir.",
            "models": [],
        }
