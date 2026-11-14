-- Şema v4 — puanlama kuralı keşfi (M5).
-- LLM önerileri, parça parça ham çıktılarıyla, doğrulama sonuçlarıyla ve gerekçeleriyle
-- saklanır. Reddedilen öneriler de silinmez (neden reddedildiği izlenebilir kalır).
-- Bilgi kayıtlarına (knowledge_entries) yalnız doğrulanmış adaylar yazılır; hiçbiri CONFIRMED
-- değildir (şema v1 CHECK'i bunu zaten zorlar).

-- Madde içermeyen başvuru belgeleri (ör. puanlama yönergesi): ham metin document_raw_units'te.
CREATE TABLE reference_documents (
    document_id  INTEGER PRIMARY KEY REFERENCES source_documents(id),
    inventory_id INTEGER NOT NULL REFERENCES inventories(id),
    unit_count   INTEGER NOT NULL CHECK (unit_count >= 0),
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- Örnek sonuçlar: cevaplar + belgede/kaynakta bildirilen sonuç (harf ve/veya puan).
CREATE TABLE example_sets (
    id             INTEGER PRIMARY KEY,
    inventory_id   INTEGER NOT NULL REFERENCES inventories(id),
    document_id    INTEGER NOT NULL REFERENCES source_documents(id),
    format_version INTEGER NOT NULL,
    example_count  INTEGER NOT NULL CHECK (example_count >= 1),
    evidence_id    INTEGER NOT NULL REFERENCES evidence_refs(id),
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (inventory_id, document_id)
);

CREATE TABLE example_results (
    id             INTEGER PRIMARY KEY,
    example_set_id INTEGER NOT NULL REFERENCES example_sets(id),
    seq            INTEGER NOT NULL CHECK (seq >= 0),
    example_key    TEXT NOT NULL,
    answers        TEXT NOT NULL CHECK (json_valid(answers)),
    outcome        TEXT NOT NULL CHECK (json_valid(outcome)),
    json_path      TEXT NOT NULL,
    UNIQUE (example_set_id, seq),
    UNIQUE (example_set_id, example_key)
);

CREATE TABLE discovery_runs (
    id              INTEGER PRIMARY KEY,
    inventory_id    INTEGER NOT NULL REFERENCES inventories(id),
    model           TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    think           INTEGER NOT NULL CHECK (think IN (0, 1)),
    status          TEXT NOT NULL CHECK (status IN ('running', 'completed',
                                                    'completed_with_errors', 'failed')),
    chunk_count     INTEGER NOT NULL DEFAULT 0,
    candidate_count INTEGER NOT NULL DEFAULT 0,
    accepted_count  INTEGER NOT NULL DEFAULT 0,
    rejected_count  INTEGER NOT NULL DEFAULT 0,
    message         TEXT,
    meta            TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(meta)),
    started_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    finished_at     TEXT
);

CREATE TABLE discovery_chunks (
    id            INTEGER PRIMARY KEY,
    run_id        INTEGER NOT NULL REFERENCES discovery_runs(id),
    document_id   INTEGER REFERENCES source_documents(id),
    chunk_index   INTEGER NOT NULL CHECK (chunk_index >= 0),
    char_start    INTEGER NOT NULL,
    char_end      INTEGER NOT NULL CHECK (char_end >= char_start),
    prompt_sha256 TEXT NOT NULL CHECK (length(prompt_sha256) = 64),
    status        TEXT NOT NULL CHECK (status IN ('ok', 'invalid_output', 'llm_error')),
    raw_output    TEXT,
    thinking      TEXT,
    error         TEXT,
    eval_count    INTEGER,
    duration_ms   INTEGER,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX ix_dchunks_run ON discovery_chunks(run_id);

CREATE TABLE discovery_candidates (
    id            INTEGER PRIMARY KEY,
    run_id        INTEGER NOT NULL REFERENCES discovery_runs(id),
    chunk_id      INTEGER NOT NULL REFERENCES discovery_chunks(id),
    seq           INTEGER NOT NULL CHECK (seq >= 0),
    raw           TEXT NOT NULL,                  -- LLM'in önerdiği nesne, birebir
    rule_type     TEXT,
    rule_key      TEXT,
    body          TEXT CHECK (body IS NULL OR json_valid(body)),
    quote         TEXT,                           -- LLM'in verdiği alıntı
    status        TEXT NOT NULL CHECK (status IN ('accepted', 'rejected', 'conflict',
                                                  'conflicts_confirmed', 'previously_rejected')),
    reject_code   TEXT,
    reject_reason TEXT,
    evidence_id   INTEGER REFERENCES evidence_refs(id),  -- belgede bulunan birebir alıntı
    entry_id      INTEGER REFERENCES knowledge_entries(id),
    UNIQUE (chunk_id, seq),
    CHECK (status <> 'rejected' OR reject_code IS NOT NULL)
);
CREATE INDEX ix_dcand_run ON discovery_candidates(run_id, status);

CREATE TABLE fit_runs (
    id                INTEGER PRIMARY KEY,
    inventory_id      INTEGER NOT NULL REFERENCES inventories(id),
    discovery_run_id  INTEGER REFERENCES discovery_runs(id),
    example_count     INTEGER NOT NULL CHECK (example_count >= 0),
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE fit_results (
    id          INTEGER PRIMARY KEY,
    fit_run_id  INTEGER NOT NULL REFERENCES fit_runs(id),
    rule_key    TEXT NOT NULL,
    entry_id    INTEGER REFERENCES knowledge_entries(id),
    outcome     TEXT NOT NULL CHECK (outcome IN ('consistent', 'inconsistent', 'not_testable',
                                                 'conflict')),
    detail      TEXT NOT NULL CHECK (json_valid(detail)),
    UNIQUE (fit_run_id, rule_key)
);

-- ---------------------------------------------------------------- denetim
CREATE TRIGGER audit_discovery_run AFTER INSERT ON discovery_runs
BEGIN
    INSERT INTO audit_log(event, ref_table, ref_id, detail)
    VALUES ('discovery_started', 'discovery_runs', NEW.id,
            json_object('inventory_id', NEW.inventory_id, 'model', NEW.model,
                        'prompt_version', NEW.prompt_version));
END;

CREATE TRIGGER audit_discovery_finish AFTER UPDATE OF status ON discovery_runs
BEGIN
    INSERT INTO audit_log(event, ref_table, ref_id, detail)
    VALUES ('discovery_finished', 'discovery_runs', NEW.id,
            json_object('status', NEW.status, 'accepted', NEW.accepted_count,
                        'rejected', NEW.rejected_count));
END;

CREATE TRIGGER audit_example_set AFTER INSERT ON example_sets
BEGIN
    INSERT INTO audit_log(event, ref_table, ref_id, detail)
    VALUES ('examples_imported', 'example_sets', NEW.id,
            json_object('inventory_id', NEW.inventory_id, 'count', NEW.example_count));
END;

CREATE TRIGGER audit_reference_document AFTER INSERT ON reference_documents
BEGIN
    INSERT INTO audit_log(event, ref_table, ref_id, detail)
    VALUES ('reference_document_imported', 'source_documents', NEW.document_id,
            json_object('inventory_id', NEW.inventory_id, 'units', NEW.unit_count));
END;

-- ---------------------------------------------------------------- değişmezlik
-- Çalıştırma yalnız 'running' durumundayken bir kez sonuçlandırılabilir; kimlik alanları sabit.
CREATE TRIGGER ro_druns_u BEFORE UPDATE ON discovery_runs
WHEN OLD.status <> 'running' OR NEW.status = 'running'
     OR NEW.inventory_id IS NOT OLD.inventory_id OR NEW.model IS NOT OLD.model
     OR NEW.prompt_version IS NOT OLD.prompt_version OR NEW.think IS NOT OLD.think
     OR NEW.started_at IS NOT OLD.started_at
BEGIN SELECT RAISE(ABORT, 'keşif çalıştırması değiştirilemez (yalnız bir kez sonuçlandırılır)'); END;
CREATE TRIGGER ro_druns_d BEFORE DELETE ON discovery_runs
BEGIN SELECT RAISE(ABORT, 'keşif çalıştırması silinemez'); END;
CREATE TRIGGER ro_dchunks_u BEFORE UPDATE ON discovery_chunks
BEGIN SELECT RAISE(ABORT, 'keşif parçası değiştirilemez'); END;
CREATE TRIGGER ro_dchunks_d BEFORE DELETE ON discovery_chunks
BEGIN SELECT RAISE(ABORT, 'keşif parçası silinemez'); END;
CREATE TRIGGER ro_dcand_u BEFORE UPDATE ON discovery_candidates
BEGIN SELECT RAISE(ABORT, 'keşif adayı değiştirilemez'); END;
CREATE TRIGGER ro_dcand_d BEFORE DELETE ON discovery_candidates
BEGIN SELECT RAISE(ABORT, 'keşif adayı silinemez'); END;
CREATE TRIGGER ro_fitruns_u BEFORE UPDATE ON fit_runs
BEGIN SELECT RAISE(ABORT, 'uyum çalıştırması değiştirilemez'); END;
CREATE TRIGGER ro_fitruns_d BEFORE DELETE ON fit_runs
BEGIN SELECT RAISE(ABORT, 'uyum çalıştırması silinemez'); END;
CREATE TRIGGER ro_fitres_u BEFORE UPDATE ON fit_results
BEGIN SELECT RAISE(ABORT, 'uyum sonucu değiştirilemez'); END;
CREATE TRIGGER ro_fitres_d BEFORE DELETE ON fit_results
BEGIN SELECT RAISE(ABORT, 'uyum sonucu silinemez'); END;
CREATE TRIGGER ro_refdocs_u BEFORE UPDATE ON reference_documents
BEGIN SELECT RAISE(ABORT, 'başvuru belgesi kaydı değiştirilemez'); END;
CREATE TRIGGER ro_refdocs_d BEFORE DELETE ON reference_documents
BEGIN SELECT RAISE(ABORT, 'başvuru belgesi kaydı silinemez'); END;
CREATE TRIGGER ro_exsets_u BEFORE UPDATE ON example_sets
BEGIN SELECT RAISE(ABORT, 'örnek kümesi değiştirilemez'); END;
CREATE TRIGGER ro_exsets_d BEFORE DELETE ON example_sets
BEGIN SELECT RAISE(ABORT, 'örnek kümesi silinemez'); END;
CREATE TRIGGER ro_exres_u BEFORE UPDATE ON example_results
BEGIN SELECT RAISE(ABORT, 'örnek sonuç değiştirilemez'); END;
CREATE TRIGGER ro_exres_d BEFORE DELETE ON example_results
BEGIN SELECT RAISE(ABORT, 'örnek sonuç silinemez'); END;
