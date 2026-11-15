-- Yerel model rapor açıklamaları: puanı değiştirmez, ekleme yalnız; dışa aktarma kayıttan okur.
CREATE TABLE report_commentaries (
 id INTEGER PRIMARY KEY,
 assessment_id INTEGER NOT NULL REFERENCES assessments(id),
 facts_sha256 TEXT NOT NULL CHECK(length(facts_sha256)=64),
 model TEXT NOT NULL,
 prompt_version TEXT NOT NULL,
 body TEXT NOT NULL CHECK(json_valid(body)),
 created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX ix_commentary_assessment ON report_commentaries(assessment_id, id);
CREATE TRIGGER ro_commentary_u BEFORE UPDATE ON report_commentaries
BEGIN SELECT RAISE(ABORT, 'rapor açıklaması değiştirilemez'); END;
CREATE TRIGGER ro_commentary_d BEFORE DELETE ON report_commentaries
BEGIN SELECT RAISE(ABORT, 'rapor açıklaması silinemez'); END;
