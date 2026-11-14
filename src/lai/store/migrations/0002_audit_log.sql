-- Şema v2 — spec §6 `audit_log` ve gereksinim alan geçmişi (M3 eksik giderme).
-- audit_log: öğrenme dışı veri olaylarının salt eklenir denetim kaydı. Kişisel değerler
-- (ad, iletişim) buraya KOPYALANMAZ; yalnız değişen alan adları yazılır, böylece kişisel
-- veri ileride silinebilir/anonimleştirilebilir kalır.

CREATE TABLE audit_log (
    id         INTEGER PRIMARY KEY,
    event      TEXT NOT NULL,
    ref_table  TEXT,
    ref_id     INTEGER,
    detail     TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(detail)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX ix_audit_ref ON audit_log(ref_table, ref_id);

CREATE TRIGGER ro_audit_u BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log değiştirilemez'); END;
CREATE TRIGGER ro_audit_d BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log silinemez'); END;

CREATE TRIGGER audit_inventory AFTER INSERT ON inventories
BEGIN
    INSERT INTO audit_log(event, ref_table, ref_id, detail)
    VALUES ('inventory_created', 'inventories', NEW.id, json_object('name', NEW.name));
END;

CREATE TRIGGER audit_inventory_version AFTER INSERT ON inventory_versions
BEGIN
    INSERT INTO audit_log(event, ref_table, ref_id, detail)
    VALUES ('inventory_version_created', 'inventory_versions', NEW.id,
            json_object('inventory_id', NEW.inventory_id, 'version', NEW.version,
                        'label', NEW.label));
END;

CREATE TRIGGER audit_document AFTER INSERT ON source_documents
BEGIN
    INSERT INTO audit_log(event, ref_table, ref_id, detail)
    VALUES ('document_added', 'source_documents', NEW.id,
            json_object('inventory_id', NEW.inventory_id, 'file_name', NEW.file_name,
                        'sha256', NEW.sha256, 'kind', NEW.kind));
END;

CREATE TRIGGER audit_participant AFTER INSERT ON participants
BEGIN
    INSERT INTO audit_log(event, ref_table, ref_id, detail)
    VALUES ('participant_created', 'participants', NEW.id, json_object('code', NEW.code));
END;

CREATE TRIGGER audit_participant_update AFTER UPDATE ON participants
WHEN OLD.alias IS NOT NEW.alias OR OLD.full_name IS NOT NEW.full_name
  OR OLD.contact IS NOT NEW.contact OR OLD.category IS NOT NEW.category
  OR OLD.attributes IS NOT NEW.attributes OR OLD.code IS NOT NEW.code
BEGIN
    INSERT INTO audit_log(event, ref_table, ref_id, detail)
    VALUES ('participant_updated', 'participants', NEW.id, json_object('fields', json_array(
        CASE WHEN OLD.code IS NOT NEW.code THEN 'code' END,
        CASE WHEN OLD.alias IS NOT NEW.alias THEN 'alias' END,
        CASE WHEN OLD.full_name IS NOT NEW.full_name THEN 'full_name' END,
        CASE WHEN OLD.contact IS NOT NEW.contact THEN 'contact' END,
        CASE WHEN OLD.category IS NOT NEW.category THEN 'category' END,
        CASE WHEN OLD.attributes IS NOT NEW.attributes THEN 'attributes' END)));
END;

CREATE TRIGGER audit_response_set AFTER INSERT ON response_sets
BEGIN
    INSERT INTO audit_log(event, ref_table, ref_id, detail)
    VALUES ('response_set_created', 'response_sets', NEW.id,
            json_object('participant_id', NEW.participant_id,
                        'inventory_version_id', NEW.inventory_version_id));
END;

CREATE TRIGGER audit_profile_version AFTER INSERT ON scoring_profile_versions
BEGIN
    INSERT INTO audit_log(event, ref_table, ref_id, detail)
    VALUES ('profile_version_published', 'scoring_profile_versions', NEW.id,
            json_object('profile_id', NEW.profile_id, 'version', NEW.version,
                        'provisional', NEW.provisional));
END;

CREATE TRIGGER audit_assessment AFTER INSERT ON assessments
BEGIN
    INSERT INTO audit_log(event, ref_table, ref_id, detail)
    VALUES ('assessment_recorded', 'assessments', NEW.id,
            json_object('response_set_id', NEW.response_set_id,
                        'profile_version_id', NEW.profile_version_id, 'status', NEW.status,
                        'recalculated_from', NEW.recalculated_from));
END;

CREATE TRIGGER audit_feedback AFTER INSERT ON assessment_feedback
BEGIN
    INSERT INTO audit_log(event, ref_table, ref_id, detail)
    VALUES ('assessment_feedback', 'assessment_feedback', NEW.id,
            json_object('assessment_id', NEW.assessment_id));
END;

-- Gereksinimlerde durum dışındaki alan değişiklikleri de öğrenme geçmişine yazılır.
CREATE TRIGGER history_requirement_fields AFTER UPDATE ON requirements
WHEN OLD.description IS NOT NEW.description OR OLD.critical IS NOT NEW.critical
  OR OLD.category IS NOT NEW.category OR OLD.dimension_code IS NOT NEW.dimension_code
  OR OLD.item_number IS NOT NEW.item_number
BEGIN
    INSERT INTO learning_history(inventory_id, event, ref_table, ref_id, detail)
    VALUES (NEW.inventory_id, 'requirement_changed', 'requirements', NEW.id,
            json_object('req_key', NEW.req_key,
                        'from', json_object('description', OLD.description,
                                            'critical', OLD.critical, 'category', OLD.category,
                                            'dimension_code', OLD.dimension_code,
                                            'item_number', OLD.item_number),
                        'to', json_object('description', NEW.description,
                                          'critical', NEW.critical, 'category', NEW.category,
                                          'dimension_code', NEW.dimension_code,
                                          'item_number', NEW.item_number)));
END;
