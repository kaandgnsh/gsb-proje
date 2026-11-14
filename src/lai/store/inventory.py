"""Envanter, sürümler, kaynak belgeler, kanıtlar, boyutlar, maddeler."""
from __future__ import annotations

from collections.abc import Iterable


class InventoryRepo:
    def __init__(self, store):
        self.s = store

    def create_inventory(self, name: str, description: str | None = None) -> int:
        with self.s.transaction() as c:
            return c.execute("INSERT INTO inventories(name, description) VALUES (?, ?)",
                             (name, description)).lastrowid

    def add_version(self, inventory_id: int, label: str | None = None) -> int:
        with self.s.transaction() as c:
            v = c.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM inventory_versions "
                          "WHERE inventory_id = ?", (inventory_id,)).fetchone()[0]
            return c.execute("INSERT INTO inventory_versions(inventory_id, version, label) "
                             "VALUES (?, ?, ?)", (inventory_id, v, label)).lastrowid

    def add_document(self, inventory_id: int, *, file_name: str, sha256: str, kind: str,
                     size_bytes: int | None = None, stored_path: str | None = None) -> tuple[int, bool]:
        """Aynı içerik (sha256) ikinci kez eklenmez. Dönüş: (id, yeni mi)."""
        with self.s.transaction() as c:
            row = c.execute("SELECT id FROM source_documents WHERE inventory_id=? AND sha256=?",
                            (inventory_id, sha256)).fetchone()
            if row:
                return row[0], False
            return c.execute("INSERT INTO source_documents(inventory_id, file_name, sha256, kind, "
                             "size_bytes, stored_path) VALUES (?,?,?,?,?,?)",
                             (inventory_id, file_name, sha256, kind, size_bytes,
                              stored_path)).lastrowid, True

    def add_evidence(self, *, kind: str, document_id: int | None = None, page: int | None = None,
                     char_start: int | None = None, char_end: int | None = None,
                     quote: str | None = None, note: str | None = None) -> int:
        with self.s.transaction() as c:
            return c.execute("INSERT INTO evidence_refs(kind, document_id, page, char_start, "
                             "char_end, quote, note) VALUES (?,?,?,?,?,?,?)",
                             (kind, document_id, page, char_start, char_end, quote,
                              note)).lastrowid

    def get_evidence(self, evidence_id: int) -> dict:
        row = self.s.conn.execute("SELECT * FROM evidence_refs WHERE id=?", (evidence_id,)).fetchone()
        if row is None:
            raise KeyError(evidence_id)
        return dict(row)

    def add_dimension(self, inventory_id: int, code: str, name: str | None = None,
                      poles: Iterable[tuple[str, str | None]] = ()) -> int:
        with self.s.transaction() as c:
            did = c.execute("INSERT INTO dimensions(inventory_id, code, name) VALUES (?,?,?)",
                            (inventory_id, code, name)).lastrowid
            c.executemany("INSERT INTO poles(dimension_id, code, name) VALUES (?,?,?)",
                          [(did, pc, pn) for pc, pn in poles])
            return did

    def dimensions(self, inventory_id: int) -> list[dict]:
        rows = self.s.conn.execute(
            "SELECT d.code, d.name, group_concat(p.code, '') AS poles FROM dimensions d "
            "LEFT JOIN poles p ON p.dimension_id = d.id WHERE d.inventory_id = ? "
            "GROUP BY d.id ORDER BY d.id", (inventory_id,)).fetchall()
        return [dict(r) for r in rows]

    def add_items(self, inventory_version_id: int, items: Iterable[dict]) -> int:
        """items: {number, text, options: [(code, text), ...], evidence_id?}. Dönüş: eklenen sayı."""
        n = 0
        with self.s.transaction() as c:
            for it in items:
                iid = c.execute("INSERT INTO items(inventory_version_id, number, text, evidence_id) "
                                "VALUES (?,?,?,?)", (inventory_version_id, it["number"], it["text"],
                                                    it.get("evidence_id"))).lastrowid
                c.executemany("INSERT INTO options(item_id, code, text, position) VALUES (?,?,?,?)",
                              [(iid, code, text, pos) for pos, (code, text)
                               in enumerate(it.get("options", []))])
                n += 1
        return n

    def items(self, inventory_version_id: int) -> list[dict]:
        rows = self.s.conn.execute("SELECT id, number, text FROM items WHERE inventory_version_id=? "
                                   "ORDER BY number", (inventory_version_id,)).fetchall()
        opts: dict[int, list] = {}
        for o in self.s.conn.execute(
                "SELECT o.item_id, o.code, o.text FROM options o JOIN items i ON i.id = o.item_id "
                "WHERE i.inventory_version_id=? ORDER BY o.item_id, o.position",
                (inventory_version_id,)):
            opts.setdefault(o[0], []).append((o[1], o[2]))
        return [{"number": r["number"], "text": r["text"], "options": opts.get(r["id"], [])}
                for r in rows]
