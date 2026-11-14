"""Veritabanı erişim noktası: bağlantı, işlemler ve depolar."""
from __future__ import annotations

import itertools
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .db import migrate, open_db


class Store:
    def __init__(self, conn: sqlite3.Connection, path: Path | None = None):
        self.conn = conn
        self.path = path
        self._depth = 0
        self._sp = itertools.count()
        from .assessments import AssessmentRepo
        from .inventory import InventoryRepo
        from .knowledge import KnowledgeRepo
        from .participants import ParticipantRepo
        from .profiles import ProfileRepo
        from .questions import QuestionRepo
        self.inventory = InventoryRepo(self)
        self.knowledge = KnowledgeRepo(self)
        self.questions = QuestionRepo(self)
        self.profiles = ProfileRepo(self)
        self.participants = ParticipantRepo(self)
        self.assessments = AssessmentRepo(self)

    @classmethod
    def open(cls, path: Path, *, backup_dir: Path | None = None) -> Store:
        conn = open_db(path)
        migrate(conn, backup_dir=backup_dir)
        return cls(conn, Path(path))

    @contextmanager
    def transaction(self):
        """İç içe çağrılabilir; en dıştaki BEGIN IMMEDIATE, içtekiler SAVEPOINT kullanır."""
        if self._depth == 0:
            self.conn.execute("BEGIN IMMEDIATE")
            name = None
        else:
            name = f"sp{next(self._sp)}"
            self.conn.execute(f"SAVEPOINT {name}")
        self._depth += 1
        try:
            yield self.conn
        except BaseException:
            self._depth -= 1
            if name:
                self.conn.execute(f"ROLLBACK TO {name}")
                self.conn.execute(f"RELEASE {name}")
            else:
                self.conn.execute("ROLLBACK")
            raise
        else:
            self._depth -= 1
            self.conn.execute(f"RELEASE {name}" if name else "COMMIT")

    def close(self) -> None:
        self.conn.close()
