"""İptal isteği güvenli hizmet sınırında uygulanır; QThread zorla sonlandırılmaz."""

import threading
from dataclasses import asdict, is_dataclass

from PySide6.QtCore import QThread, Signal


class Task(QThread):
    done = Signal(object)
    failed = Signal(object)
    progress = Signal(object)

    def __init__(self, backend, operation, args, parent=None):
        super().__init__(parent)
        self.backend = backend
        self.operation = operation
        self.args = args
        self.cancel = threading.Event()

    def request_cancel(self, action="pause"):
        if self.operation == "run_job" and (
            self.args.get("cancel_action") != "cancel" or action == "cancel"
        ):
            self.args["cancel_action"] = action
        self.cancel.set()

    def run(self):
        try:
            self.done.emit(
                self.backend.execute(
                    self.operation, self.args, self.cancel, self.progress.emit
                )
            )
        except Exception as e:  # noqa: BLE001 — arayüzde kaynaklı hata gösterilir, worker kapanır.
            self.failed.emit(
                {
                    "message": str(e),
                    "issues": [
                        asdict(i) if is_dataclass(i) else str(i)
                        for i in getattr(e, "issues", [])
                    ],
                }
            )
