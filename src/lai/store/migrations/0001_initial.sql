-- Local Assessment AI — şema v1
-- İlke: öğrenilen bilgi, düzeltmeler, geçmiş, yayımlanmış profiller ve değerlendirmeler
-- SALT EKLENİR. Değişmezlik ve güven kuralları uygulama kodundan bağımsız olarak burada
-- (CHECK + tetikleyici) zorlanır. Zaman damgaları UTC ISO-8601.

CREATE TABLE app_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- ---------------------------------------------------------------- envanter ve kaynaklar
CREATE TABLE inventories (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE inventory_versions (
    id           INTEGER PRIMARY KEY,
    inventory_id INTEGER NOT NULL REFERENCES inventories(id),
    version      INTEGER NOT NULL CHECK (version >= 1),
    label        TEXT,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (inventory_id, version)
);

CREATE TABLE source_documents (
    id           INTEGER PRIMARY KEY,
    inventory_id INTEGER NOT NULL REFERENCES inventories(id),
    file_name    TEXT NOT NULL,
    sha256       TEXT NOT NULL CHECK (length(sha256) = 64),
    kind         TEXT NOT NULL,             -- pdf | docx | xlsx | csv | json | txt
    size_bytes   INTEGER,
    stored_path  TEXT,                      -- uygulama veri dizinindeki kopya
    imported_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (inventory_id, sha256)
);

CREATE TABLE evidence_refs (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN ('document', 'user_statement', 'example_result',
                                              'assessment_feedback')),
    document_id INTEGER REFERENCES source_documents(id),
    page        INTEGER,
    char_start  INTEGER,
    char_end    INTEGER,
    quote       TEXT,
    note        TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (kind <> 'document' OR (document_id IS NOT NULL AND quote IS NOT NULL)),
    CHECK (char_start IS NULL OR char_end IS NULL OR char_end >= char_start)
);

CREATE TABLE dimensions (
    id           INTEGER PRIMARY KEY,
    inventory_id INTEGER NOT NULL REFERENCES inventories(id),
    code         TEXT NOT NULL,             -- ör. E-I (sabit değil, keşfedilir)
    name         TEXT,
    UNIQUE (inventory_id, code)
);

CREATE TABLE poles (
    id           INTEGER PRIMARY KEY,
    dimension_id INTEGER NOT NULL REFERENCES dimensions(id),
    code         TEXT NOT NULL,             -- ör. E
    name         TEXT,
    UNIQUE (dimension_id, code)
);

CREATE TABLE items (
    id                   INTEGER PRIMARY KEY,
    inventory_version_id INTEGER NOT NULL REFERENCES inventory_versions(id),
    number               INTEGER NOT NULL CHECK (number >= 1),
    text                 TEXT NOT NULL,
    evidence_id          INTEGER REFERENCES evidence_refs(id),
    UNIQUE (inventory_version_id, number)
);

CREATE TABLE options (
    id       INTEGER PRIMARY KEY,
    item_id  INTEGER NOT NULL REFERENCES items(id),
    code     TEXT NOT NULL,
    text     TEXT NOT NULL,
    position INTEGER NOT NULL,
    UNIQUE (item_id, code)
);

-- ---------------------------------------------------------------- bilgi (salt eklenir)
CREATE TABLE knowledge_entries (
    id               INTEGER PRIMARY KEY,
    inventory_id     INTEGER NOT NULL REFERENCES inventories(id),
    entry_key        TEXT NOT NULL,         -- mantıksal kimlik, ör. rule:reverse:item:12
    version          INTEGER NOT NULL CHECK (version >= 1),
    supersedes_id    INTEGER REFERENCES knowledge_entries(id),
    kind             TEXT NOT NULL CHECK (kind IN ('fact', 'rule', 'note')),
    rule_type        TEXT,
    content          TEXT NOT NULL CHECK (json_valid(content)),
    summary          TEXT NOT NULL,
    item_number      INTEGER,
    dimension_code   TEXT,
    confidence       TEXT NOT NULL CHECK (confidence IN ('CONFIRMED', 'STRONGLY_INFERRED',
                                                         'WEAKLY_INFERRED', 'UNKNOWN',
                                                         'CONTRADICTORY')),
    source           TEXT NOT NULL CHECK (source IN ('document', 'user', 'ai_inference',
                                                     'example_fit', 'assessment_feedback')),
    validation       TEXT NOT NULL DEFAULT 'pending'
                         CHECK (validation IN ('pending', 'validated', 'rejected')),
    confirmation_ref TEXT,
    reason           TEXT,
    created_by       TEXT NOT NULL DEFAULT 'system',
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (inventory_id, entry_key, version),
    CHECK ((version = 1) = (supersedes_id IS NULL)),
    CHECK (kind <> 'rule' OR rule_type IS NOT NULL),
    -- Belirsiz bilgi kendiliğinden doğrulanmış sayılamaz: CONFIRMED yalnız kullanıcı ya da
    -- belge kaynağından, onay referansı ve doğrulama ile.
    CHECK (confidence <> 'CONFIRMED'
           OR (source IN ('document', 'user') AND confirmation_ref IS NOT NULL
               AND validation = 'validated')),
    CHECK (validation <> 'validated' OR source IN ('document', 'user'))
);
-- Bir sürüm en fazla bir kez geçersiz kılınır: sürüm zincirinde dal oluşamaz.
CREATE UNIQUE INDEX ux_knowledge_supersedes ON knowledge_entries(supersedes_id)
    WHERE supersedes_id IS NOT NULL;
CREATE INDEX ix_knowledge_key ON knowledge_entries(inventory_id, entry_key);

CREATE TRIGGER knowledge_chain_check BEFORE INSERT ON knowledge_entries
WHEN NEW.supersedes_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'yeni sürüm, aynı anahtarın bir önceki sürümünün üstüne kurulmalı')
    WHERE NOT EXISTS (
        SELECT 1 FROM knowledge_entries k
        WHERE k.id = NEW.supersedes_id AND k.inventory_id = NEW.inventory_id
          AND k.entry_key = NEW.entry_key AND k.version = NEW.version - 1);
END;

CREATE TABLE knowledge_evidence (
    entry_id    INTEGER NOT NULL REFERENCES knowledge_entries(id),
    evidence_id INTEGER NOT NULL REFERENCES evidence_refs(id),
    relation    TEXT NOT NULL DEFAULT 'supports' CHECK (relation IN ('supports', 'contradicts')),
    PRIMARY KEY (entry_id, evidence_id)
);

CREATE TABLE user_corrections (
    id                 INTEGER PRIMARY KEY,
    inventory_id       INTEGER NOT NULL REFERENCES inventories(id),
    target_entry_id    INTEGER REFERENCES knowledge_entries(id),   -- düzeltilen (varsa)
    resulting_entry_id INTEGER NOT NULL REFERENCES knowledge_entries(id),
    evidence_id        INTEGER NOT NULL REFERENCES evidence_refs(id),
    explanation        TEXT NOT NULL,
    user_text          TEXT,
    created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- ---------------------------------------------------------------- bütünlük matrisi, sorular
CREATE TABLE requirements (
    id             INTEGER PRIMARY KEY,
    inventory_id   INTEGER NOT NULL REFERENCES inventories(id),
    req_key        TEXT NOT NULL,
    category       TEXT NOT NULL CHECK (category IN (
                       'answer_mapping', 'item_weight', 'reverse_scoring', 'dimension_relation',
                       'normalization', 'threshold', 'zero_score', 'missing_answer_policy',
                       'example_consistency', 'instruction_conflict', 'tie_break', 'other')),
    description    TEXT NOT NULL,
    dimension_code TEXT,
    item_number    INTEGER,
    critical       INTEGER NOT NULL CHECK (critical IN (0, 1)),
    status         TEXT NOT NULL CHECK (status IN ('CONFIRMED', 'STRONGLY_INFERRED',
                                                   'WEAKLY_INFERRED', 'UNKNOWN', 'CONTRADICTORY')),
    status_reason  TEXT,
    updated_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (inventory_id, req_key)
);

CREATE TABLE clarification_questions (
    id               INTEGER PRIMARY KEY,
    inventory_id     INTEGER NOT NULL REFERENCES inventories(id),
    dedupe_key       TEXT NOT NULL,
    group_key        TEXT NOT NULL,
    requirement_id   INTEGER REFERENCES requirements(id),
    missing_info     TEXT NOT NULL,          -- 1. eksik bilgi
    why_it_matters   TEXT NOT NULL,          -- 2. neden önemli
    related          TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(related)),  -- 3. madde/boyut
    evidence_summary TEXT NOT NULL DEFAULT '',                                -- 4. kanıt
    impact           TEXT NOT NULL,          -- 5. etkilenebilecek sonuç
    question_text    TEXT NOT NULL,          -- 6. kullanıcıya soru
    choices          TEXT CHECK (choices IS NULL OR json_valid(choices)),
    status           TEXT NOT NULL DEFAULT 'open'
                         CHECK (status IN ('open', 'answered', 'dont_know', 'later', 'skipped')),
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (inventory_id, dedupe_key)
);

CREATE TABLE question_responses (
    id          INTEGER PRIMARY KEY,
    question_id INTEGER NOT NULL REFERENCES clarification_questions(id),
    kind        TEXT NOT NULL CHECK (kind IN ('choice', 'text', 'dont_know', 'later', 'skip')),
    value       TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (kind NOT IN ('choice', 'text') OR (value IS NOT NULL AND length(trim(value)) > 0))
);

CREATE TRIGGER question_response_sets_status AFTER INSERT ON question_responses
BEGIN
    UPDATE clarification_questions
       SET status = CASE NEW.kind WHEN 'choice' THEN 'answered' WHEN 'text' THEN 'answered'
                                  WHEN 'dont_know' THEN 'dont_know' WHEN 'later' THEN 'later'
                                  ELSE 'skipped' END,
           updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
     WHERE id = NEW.question_id;
END;

-- ---------------------------------------------------------------- öğrenme geçmişi
CREATE TABLE learning_history (
    id           INTEGER PRIMARY KEY,
    inventory_id INTEGER REFERENCES inventories(id),
    event        TEXT NOT NULL,
    ref_table    TEXT,
    ref_id       INTEGER,
    detail       TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(detail)),
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX ix_history_inventory ON learning_history(inventory_id, id);

CREATE TRIGGER history_knowledge AFTER INSERT ON knowledge_entries
BEGIN
    INSERT INTO learning_history(inventory_id, event, ref_table, ref_id, detail)
    VALUES (NEW.inventory_id,
            CASE WHEN NEW.version = 1 THEN 'knowledge_created' ELSE 'knowledge_revised' END,
            'knowledge_entries', NEW.id,
            json_object('entry_key', NEW.entry_key, 'version', NEW.version,
                        'confidence', NEW.confidence, 'source', NEW.source,
                        'validation', NEW.validation, 'reason', NEW.reason));
END;

CREATE TRIGGER history_correction AFTER INSERT ON user_corrections
BEGIN
    INSERT INTO learning_history(inventory_id, event, ref_table, ref_id, detail)
    VALUES (NEW.inventory_id, 'user_correction', 'user_corrections', NEW.id,
            json_object('target_entry_id', NEW.target_entry_id,
                        'resulting_entry_id', NEW.resulting_entry_id,
                        'explanation', NEW.explanation));
END;

CREATE TRIGGER history_requirement_new AFTER INSERT ON requirements
BEGIN
    INSERT INTO learning_history(inventory_id, event, ref_table, ref_id, detail)
    VALUES (NEW.inventory_id, 'requirement_added', 'requirements', NEW.id,
            json_object('req_key', NEW.req_key, 'status', NEW.status,
                        'critical', NEW.critical));
END;

CREATE TRIGGER history_requirement_status AFTER UPDATE OF status ON requirements
WHEN OLD.status <> NEW.status
BEGIN
    INSERT INTO learning_history(inventory_id, event, ref_table, ref_id, detail)
    VALUES (NEW.inventory_id, 'requirement_status', 'requirements', NEW.id,
            json_object('req_key', NEW.req_key, 'from', OLD.status, 'to', NEW.status,
                        'reason', NEW.status_reason));
END;

CREATE TRIGGER history_question_new AFTER INSERT ON clarification_questions
BEGIN
    INSERT INTO learning_history(inventory_id, event, ref_table, ref_id, detail)
    VALUES (NEW.inventory_id, 'question_asked', 'clarification_questions', NEW.id,
            json_object('dedupe_key', NEW.dedupe_key));
END;

CREATE TRIGGER history_question_response AFTER INSERT ON question_responses
BEGIN
    INSERT INTO learning_history(inventory_id, event, ref_table, ref_id, detail)
    SELECT q.inventory_id, 'question_response', 'question_responses', NEW.id,
           json_object('question_id', NEW.question_id, 'kind', NEW.kind)
      FROM clarification_questions q WHERE q.id = NEW.question_id;
END;

-- ---------------------------------------------------------------- puanlama profilleri
CREATE TABLE scoring_profiles (
    id           INTEGER PRIMARY KEY,
    inventory_id INTEGER NOT NULL REFERENCES inventories(id),
    name         TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (inventory_id, name)
);

CREATE TABLE scoring_profile_versions (
    id           INTEGER PRIMARY KEY,
    profile_id   INTEGER NOT NULL REFERENCES scoring_profiles(id),
    version      INTEGER NOT NULL CHECK (version >= 1),
    snapshot     TEXT NOT NULL CHECK (json_valid(snapshot)),
    content_hash TEXT NOT NULL CHECK (length(content_hash) = 64),
    provisional  INTEGER NOT NULL CHECK (provisional IN (0, 1)),
    note         TEXT,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (profile_id, version)
);

CREATE TABLE profile_version_entries (
    profile_version_id INTEGER NOT NULL REFERENCES scoring_profile_versions(id),
    entry_id           INTEGER NOT NULL REFERENCES knowledge_entries(id),
    PRIMARY KEY (profile_version_id, entry_id)
);

CREATE TRIGGER history_profile_version AFTER INSERT ON scoring_profile_versions
BEGIN
    INSERT INTO learning_history(inventory_id, event, ref_table, ref_id, detail)
    SELECT p.inventory_id, 'profile_published', 'scoring_profile_versions', NEW.id,
           json_object('version', NEW.version, 'provisional', NEW.provisional,
                       'content_hash', NEW.content_hash, 'note', NEW.note)
      FROM scoring_profiles p WHERE p.id = NEW.profile_id;
END;

-- ---------------------------------------------------------------- katılımcılar, cevaplar
CREATE TABLE participants (
    id         INTEGER PRIMARY KEY,
    code       TEXT NOT NULL UNIQUE,       -- anonim kimlik; tek zorunlu alan
    alias      TEXT,
    full_name  TEXT,
    contact    TEXT,
    category   TEXT,                       -- sabit değil (öğrenci, danışan, …)
    attributes TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(attributes)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE response_sets (
    id                   INTEGER PRIMARY KEY,
    participant_id       INTEGER NOT NULL REFERENCES participants(id),
    inventory_version_id INTEGER NOT NULL REFERENCES inventory_versions(id),
    source_label         TEXT,
    created_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE responses (
    response_set_id INTEGER NOT NULL REFERENCES response_sets(id),
    item_number     INTEGER NOT NULL CHECK (item_number >= 1),
    option_code     TEXT,                  -- NULL = cevapsız
    PRIMARY KEY (response_set_id, item_number)
) WITHOUT ROWID;

-- ---------------------------------------------------------------- değerlendirmeler
CREATE TABLE assessments (
    id                 INTEGER PRIMARY KEY,
    response_set_id    INTEGER NOT NULL REFERENCES response_sets(id),
    profile_version_id INTEGER NOT NULL REFERENCES scoring_profile_versions(id),
    status             TEXT NOT NULL CHECK (status IN ('PROVISIONAL', 'VERIFIED')),
    results            TEXT NOT NULL CHECK (json_valid(results)),
    limitations        TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(limitations)),
    engine_version     TEXT NOT NULL,
    input_hash         TEXT NOT NULL CHECK (length(input_hash) = 64),
    recalculated_from  INTEGER REFERENCES assessments(id),
    created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TRIGGER assessment_verified_needs_final_profile BEFORE INSERT ON assessments
WHEN NEW.status = 'VERIFIED'
BEGIN
    SELECT RAISE(ABORT, 'geçici (provisional) profil sürümüyle VERIFIED değerlendirme kaydedilemez')
    WHERE (SELECT provisional FROM scoring_profile_versions WHERE id = NEW.profile_version_id) <> 0;
END;

CREATE TABLE assessment_feedback (
    id            INTEGER PRIMARY KEY,
    assessment_id INTEGER NOT NULL REFERENCES assessments(id),
    feedback      TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- ---------------------------------------------------------------- değişmezlik
CREATE TRIGGER ro_knowledge_u BEFORE UPDATE ON knowledge_entries
BEGIN SELECT RAISE(ABORT, 'knowledge_entries salt eklenir: yeni sürüm oluşturun'); END;
CREATE TRIGGER ro_knowledge_d BEFORE DELETE ON knowledge_entries
BEGIN SELECT RAISE(ABORT, 'knowledge_entries silinemez'); END;
CREATE TRIGGER ro_kevidence_u BEFORE UPDATE ON knowledge_evidence
BEGIN SELECT RAISE(ABORT, 'knowledge_evidence değiştirilemez'); END;
CREATE TRIGGER ro_kevidence_d BEFORE DELETE ON knowledge_evidence
BEGIN SELECT RAISE(ABORT, 'knowledge_evidence silinemez'); END;
CREATE TRIGGER ro_evidence_u BEFORE UPDATE ON evidence_refs
BEGIN SELECT RAISE(ABORT, 'evidence_refs değiştirilemez'); END;
CREATE TRIGGER ro_evidence_d BEFORE DELETE ON evidence_refs
BEGIN SELECT RAISE(ABORT, 'evidence_refs silinemez'); END;
CREATE TRIGGER ro_corrections_u BEFORE UPDATE ON user_corrections
BEGIN SELECT RAISE(ABORT, 'user_corrections değiştirilemez'); END;
CREATE TRIGGER ro_corrections_d BEFORE DELETE ON user_corrections
BEGIN SELECT RAISE(ABORT, 'user_corrections silinemez'); END;
CREATE TRIGGER ro_history_u BEFORE UPDATE ON learning_history
BEGIN SELECT RAISE(ABORT, 'learning_history değiştirilemez'); END;
CREATE TRIGGER ro_history_d BEFORE DELETE ON learning_history
BEGIN SELECT RAISE(ABORT, 'learning_history silinemez'); END;
CREATE TRIGGER ro_qresp_u BEFORE UPDATE ON question_responses
BEGIN SELECT RAISE(ABORT, 'question_responses değiştirilemez'); END;
CREATE TRIGGER ro_qresp_d BEFORE DELETE ON question_responses
BEGIN SELECT RAISE(ABORT, 'question_responses silinemez'); END;
CREATE TRIGGER ro_spv_u BEFORE UPDATE ON scoring_profile_versions
BEGIN SELECT RAISE(ABORT, 'yayımlanmış profil sürümü değiştirilemez'); END;
CREATE TRIGGER ro_spv_d BEFORE DELETE ON scoring_profile_versions
BEGIN SELECT RAISE(ABORT, 'yayımlanmış profil sürümü silinemez'); END;
CREATE TRIGGER ro_pve_u BEFORE UPDATE ON profile_version_entries
BEGIN SELECT RAISE(ABORT, 'profile_version_entries değiştirilemez'); END;
CREATE TRIGGER ro_pve_d BEFORE DELETE ON profile_version_entries
BEGIN SELECT RAISE(ABORT, 'profile_version_entries silinemez'); END;
CREATE TRIGGER ro_responses_u BEFORE UPDATE ON responses
BEGIN SELECT RAISE(ABORT, 'cevaplar değiştirilemez: yeni cevap seti oluşturun'); END;
CREATE TRIGGER ro_responses_d BEFORE DELETE ON responses
BEGIN SELECT RAISE(ABORT, 'cevaplar silinemez'); END;
CREATE TRIGGER ro_rsets_u BEFORE UPDATE ON response_sets
BEGIN SELECT RAISE(ABORT, 'cevap seti değiştirilemez'); END;
CREATE TRIGGER ro_rsets_d BEFORE DELETE ON response_sets
BEGIN SELECT RAISE(ABORT, 'cevap seti silinemez'); END;
CREATE TRIGGER ro_assess_u BEFORE UPDATE ON assessments
BEGIN SELECT RAISE(ABORT, 'değerlendirme kaydı değiştirilemez: yeniden hesaplama yeni kayıt üretir'); END;
CREATE TRIGGER ro_assess_d BEFORE DELETE ON assessments
BEGIN SELECT RAISE(ABORT, 'değerlendirme kaydı silinemez'); END;
CREATE TRIGGER ro_feedback_u BEFORE UPDATE ON assessment_feedback
BEGIN SELECT RAISE(ABORT, 'geri bildirim değiştirilemez'); END;
CREATE TRIGGER ro_feedback_d BEFORE DELETE ON assessment_feedback
BEGIN SELECT RAISE(ABORT, 'geri bildirim silinemez'); END;
CREATE TRIGGER ro_questions_d BEFORE DELETE ON clarification_questions
BEGIN SELECT RAISE(ABORT, 'netleştirme soruları silinemez'); END;
CREATE TRIGGER ro_requirements_d BEFORE DELETE ON requirements
BEGIN SELECT RAISE(ABORT, 'gereksinimler silinemez'); END;
-- Yalnız soru durumu (tetikleyiciyle) ve güncelleme zamanı değişebilir; içerik sabittir.
CREATE TRIGGER ro_questions_content BEFORE UPDATE ON clarification_questions
WHEN NEW.missing_info IS NOT OLD.missing_info OR NEW.why_it_matters IS NOT OLD.why_it_matters
  OR NEW.question_text IS NOT OLD.question_text OR NEW.impact IS NOT OLD.impact
  OR NEW.related IS NOT OLD.related OR NEW.choices IS NOT OLD.choices
  OR NEW.dedupe_key IS NOT OLD.dedupe_key OR NEW.inventory_id IS NOT OLD.inventory_id
BEGIN SELECT RAISE(ABORT, 'soru içeriği değiştirilemez'); END;

-- ---------------------------------------------------------------- görünümler
CREATE VIEW current_knowledge AS
SELECT k.* FROM knowledge_entries k
 WHERE NOT EXISTS (SELECT 1 FROM knowledge_entries n WHERE n.supersedes_id = k.id);

CREATE VIEW confirmed_rules AS
SELECT * FROM current_knowledge WHERE kind = 'rule' AND confidence = 'CONFIRMED';

CREATE VIEW inferred_rules AS
SELECT * FROM current_knowledge
 WHERE kind = 'rule' AND confidence IN ('STRONGLY_INFERRED', 'WEAKLY_INFERRED')
   AND validation <> 'rejected';

CREATE VIEW unresolved_knowledge AS
SELECT * FROM current_knowledge WHERE confidence IN ('UNKNOWN', 'CONTRADICTORY');

CREATE VIEW pending_questions AS
SELECT * FROM clarification_questions WHERE status IN ('open', 'later')
 ORDER BY group_key, id;

INSERT INTO app_meta(key, value) VALUES ('database_id', lower(hex(randomblob(16))));
INSERT INTO app_meta(key, value) VALUES ('created_at', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
