"""Mevcut grafik kabul harness'ini çekirdek kanıtıyla birlikte çalıştır."""

import json
import runpy
import sys
from pathlib import Path

from lai_runtime.sandbox import require_isolation

proof, script, *args = sys.argv[1:]
Path(proof).parent.mkdir(parents=True, exist_ok=True)
Path(proof).write_text(json.dumps(require_isolation(), indent=2))
sys.argv = [script, *args]
runpy.run_path(script, run_name="__main__")
