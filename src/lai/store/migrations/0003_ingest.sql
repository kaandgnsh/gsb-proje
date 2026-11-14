-- Şema v3 — belge içe aktarma (M4).
-- Ham içerik (document_raw_units) ile ayrıştırılmış veri (parsed_*) AYRI tablolardadır.
-- Hepsi salt eklenir: bir içe aktarma çalıştırmasının sonucu sonradan değiştirilemez.
-- Yayımlanmış envanter sürümünün maddeleri ve seçenekleri de değişmez.

ALTER TABLE evidence_refs ADD COLUMN locator TEXT CHECK (locator IS NULL OR json_valid(locator));
ALTER TABLE options ADD COLUMN evidence_id INTEGER REFERENCES evidence_refs(id);
ALTER TABLE inventory_versions ADD COLUMN source_run_id INTEGER REFERENCES import_runs(id);
ALTER TABLE inventory_versions ADD COLUMN content_hash TEXT
    CHECK (content_hash IS NULL OR length(content_hash) = 64);

-- Belgeden okunan ham metin birimleri (PDF satırı, DOCX paragrafı, tablo satırı, …), birebir.
CREATE TABLE document_raw_units (
    document_id INTEGER NOT NULL REFERENCES source_documents(id),
    idx         INTEGER NOT NULL CHECK (idx >= 0),
    page        INTEGER,
    text        TEXT NOT NULL,
    locator     TEXT NOT NULL CHECK (json_valid(locator)),
    PRIMARY KEY (document_id, idx)
) WITHOUT ROWID;

CREATE TABLE import_runs (
    id                   INTEGER PRIMARY KEY,
    inventory_id         INTEGER NOT NULL REFERENCES inventories(id),
    document_id          INTEGER REFERENCES source_documents(id),
    file_name            TEXT NOT NULL,
    sha256               TEXT NOT NULL CHECK (length(sha256) = 64),
    kind                 TEXT NOT NULL,
    parser_version       TEXT NOT NULL,
    status               TEXT NOT NULL CHECK (status IN ('imported', 'needs_review', 'failed',
                                                         'duplicate')),
    duplicate_of_run_id  INTEGER REFERENCES import_runs(id),
    inventory_version_id INTEGER REFERENCES inventory_versions(id),
    item_count           INTEGER NOT NULL DEFAULT 0,
    option_count         INTEGER NOT NULL DEFAULT 0,
    answer_key_count     INTEGER NOT NULL DEFAULT 0,
    error_count          INTEGER NOT NULL DEFAULT 0,
    warning_count        INTEGER NOT NULL DEFAULT 0,
    message              TEXT,
    meta                 TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(meta)),
    created_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (status <> 'duplicate' OR duplicate_of_run_id IS NOT NULL),
    CHECK (status <> 'imported' OR inventory_version_id IS NOT NULL)
);
CREATE INDEX ix_runs_doc ON import_runs(inventory_id, sha256, parser_version);

CREATE TABLE parsed_items (
    id            INTEGER PRIMARY KEY,
    import_run_id INTEGER NOT NULL REFERENCES import_runs(id),
    seq           INTEGER NOT NULL CHECK (seq >= 0),   -- belgedeki özgün sıra
    number        INTEGER,                            -- NULL = belgede numara yok
    label         TEXT,                               -- numaranın belgedeki yazımı
    text          TEXT NOT NULL,
    locator       TEXT NOT NULL CHECK (json_valid(locator)),
    raw_first     INTEGER,
    raw_last      INTEGER,
    UNIQUE (import_run_id, seq)
);

CREATE TABLE parsed_options (
    id             INTEGER PRIMARY KEY,
    parsed_item_id INTEGER NOT NULL REFERENCES parsed_items(id),
    seq            INTEGER NOT NULL CHECK (seq >= 0),
    code           TEXT NOT NULL,
    text           TEXT NOT NULL,
    locator        TEXT NOT NULL CHECK (json_valid(locator)),
    UNIQUE (parsed_item_id, seq)
);

-- Cevap anahtarı YORUMLANMADAN, belgedeki değeriyle saklanır.
CREATE TABLE parsed_answer_keys (
    id            INTEGER PRIMARY KEY,
    import_run_id INTEGER NOT NULL REFERENCES import_runs(id),
    seq           INTEGER NOT NULL CHECK (seq >= 0),
    item_number   INTEGER NOT NULL,
    value         TEXT NOT NULL,
    locator       TEXT NOT NULL CHECK (json_valid(locator)),
    UNIQUE (import_run_id, seq)
);

CREATE TABLE import_issues (
    id            INTEGER PRIMARY KEY,
    import_run_id INTEGER NOT NULL REFERENCES import_runs(id),
    severity      TEXT NOT NULL CHECK (severity IN ('error', 'warning', 'info')),
    code          TEXT NOT NULL,
    message       TEXT NOT NULL,
    locator       TEXT CHECK (locator IS NULL OR json_valid(locator)),
    item_seq      INTEGER,
    item_number   INTEGER
);
CREATE INDEX ix_issues_run ON import_issues(import_run_id, severity);

CREATE TRIGGER audit_import_run AFTER INSERT ON import_runs
BEGIN
    INSERT INTO audit_log(event, ref_table, ref_id, detail)
    VALUES ('document_import', 'import_runs', NEW.id,
            json_object('inventory_id', NEW.inventory_id, 'file_name', NEW.file_name,
                        'sha256', NEW.sha256, 'status', NEW.status,
                        'items', NEW.item_count, 'errors', NEW.error_count,
                        'warnings', NEW.warning_count));
END;

CREATE TRIGGER ro_rawunits_u BEFORE UPDATE ON document_raw_units
BEGIN SELECT RAISE(ABORT, 'ham belge içeriği değiştirilemez'); END;
CREATE TRIGGER ro_rawunits_d BEFORE DELETE ON document_raw_units
BEGIN SELECT RAISE(ABORT, 'ham belge içeriği silinemez'); END;
CREATE TRIGGER ro_runs_u BEFORE UPDATE ON import_runs
BEGIN SELECT RAISE(ABORT, 'içe aktarma kaydı değiştirilemez'); END;
CREATE TRIGGER ro_runs_d BEFORE DELETE ON import_runs
BEGIN SELECT RAISE(ABORT, 'içe aktarma kaydı silinemez'); END;
CREATE TRIGGER ro_pitems_u BEFORE UPDATE ON parsed_items
BEGIN SELECT RAISE(ABORT, 'ayrıştırılmış madde değiştirilemez'); END;
CREATE TRIGGER ro_pitems_d BEFORE DELETE ON parsed_items
BEGIN SELECT RAISE(ABORT, 'ayrıştırılmış madde silinemez'); END;
CREATE TRIGGER ro_popts_u BEFORE UPDATE ON parsed_options
BEGIN SELECT RAISE(ABORT, 'ayrıştırılmış seçenek değiştirilemez'); END;
CREATE TRIGGER ro_popts_d BEFORE DELETE ON parsed_options
BEGIN SELECT RAISE(ABORT, 'ayrıştırılmış seçenek silinemez'); END;
CREATE TRIGGER ro_pkeys_u BEFORE UPDATE ON parsed_answer_keys
BEGIN SELECT RAISE(ABORT, 'ayrıştırılmış cevap anahtarı değiştirilemez'); END;
CREATE TRIGGER ro_pkeys_d BEFORE DELETE ON parsed_answer_keys
BEGIN SELECT RAISE(ABORT, 'ayrıştırılmış cevap anahtarı silinemez'); END;
CREATE TRIGGER ro_issues_u BEFORE UPDATE ON import_issues
BEGIN SELECT RAISE(ABORT, 'içe aktarma sorunu değiştirilemez'); END;
CREATE TRIGGER ro_issues_d BEFORE DELETE ON import_issues
BEGIN SELECT RAISE(ABORT, 'içe aktarma sorunu silinemez'); END;
CREATE TRIGGER ro_items_u BEFORE UPDATE ON items
BEGIN SELECT RAISE(ABORT, 'envanter sürümündeki madde değiştirilemez: yeni sürüm oluşturun'); END;
CREATE TRIGGER ro_items_d BEFORE DELETE ON items
BEGIN SELECT RAISE(ABORT, 'envanter sürümündeki madde silinemez'); END;
CREATE TRIGGER ro_options_u BEFORE UPDATE ON options
BEGIN SELECT RAISE(ABORT, 'seçenek değiştirilemez: yeni sürüm oluşturun'); END;
CREATE TRIGGER ro_options_d BEFORE DELETE ON options
BEGIN SELECT RAISE(ABORT, 'seçenek silinemez'); END;
