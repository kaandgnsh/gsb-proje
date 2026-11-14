-- M8: sabit sürümler, kalıcı girdiler, katılımcı başına atomik checkpoint.
CREATE TABLE assessment_jobs (
 id INTEGER PRIMARY KEY,
 inventory_version_id INTEGER NOT NULL REFERENCES inventory_versions(id),
 profile_version_id INTEGER NOT NULL REFERENCES scoring_profile_versions(id),
 engine_version TEXT NOT NULL,
 recalculated_from_job_id INTEGER REFERENCES assessment_jobs(id),
 file_name TEXT NOT NULL, file_sha256 TEXT NOT NULL CHECK(length(file_sha256)=64),
 column_mapping TEXT NOT NULL CHECK(json_valid(column_mapping)),
 state TEXT NOT NULL CHECK(state IN ('ready','running','paused','cancelled','completed')),
 preparation_seconds REAL NOT NULL DEFAULT 0 CHECK(preparation_seconds>=0),
 processing_seconds REAL NOT NULL DEFAULT 0 CHECK(processing_seconds>=0),
 created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE assessment_job_rows (
 id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES assessment_jobs(id),
 seq INTEGER NOT NULL, sheet_name TEXT, row_number INTEGER NOT NULL,
 identity TEXT NOT NULL CHECK(json_valid(identity)),
 answers TEXT NOT NULL CHECK(json_valid(answers)),
 errors TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(errors)),
 state TEXT NOT NULL CHECK(state IN ('pending','succeeded','partial','failed')),
 assessment_id INTEGER UNIQUE REFERENCES assessments(id),
 previous_assessment_id INTEGER REFERENCES assessments(id),
 UNIQUE(job_id,seq),
 CHECK((state IN ('succeeded','partial') AND assessment_id IS NOT NULL) OR
       (state IN ('pending','failed') AND assessment_id IS NULL))
);
CREATE INDEX ix_job_pending ON assessment_job_rows(job_id,state,seq);
CREATE TRIGGER immutable_job_identity BEFORE UPDATE ON assessment_jobs
WHEN NEW.id<>OLD.id OR NEW.inventory_version_id<>OLD.inventory_version_id
 OR NEW.profile_version_id<>OLD.profile_version_id OR NEW.engine_version<>OLD.engine_version
 OR NEW.recalculated_from_job_id IS NOT OLD.recalculated_from_job_id
 OR NEW.file_name<>OLD.file_name OR NEW.file_sha256<>OLD.file_sha256 OR NEW.column_mapping<>OLD.column_mapping
BEGIN SELECT RAISE(ABORT,'iş sürümleri ve kaynakları değiştirilemez'); END;
CREATE TRIGGER immutable_job_row BEFORE UPDATE ON assessment_job_rows
WHEN OLD.state<>'pending' OR NEW.id<>OLD.id OR NEW.job_id<>OLD.job_id OR NEW.seq<>OLD.seq
 OR NEW.row_number<>OLD.row_number OR NEW.sheet_name IS NOT OLD.sheet_name
 OR NEW.identity<>OLD.identity OR NEW.answers<>OLD.answers
 OR NEW.previous_assessment_id IS NOT OLD.previous_assessment_id
BEGIN SELECT RAISE(ABORT,'iş girdisi veya tamamlanmış satır değiştirilemez'); END;
CREATE TRIGGER immutable_job_delete BEFORE DELETE ON assessment_jobs
BEGIN SELECT RAISE(ABORT,'iş silinemez'); END;
CREATE TRIGGER immutable_job_row_delete BEFORE DELETE ON assessment_job_rows
BEGIN SELECT RAISE(ABORT,'iş satırı silinemez'); END;
CREATE TRIGGER audit_assessment_job AFTER INSERT ON assessment_jobs BEGIN
 INSERT INTO audit_log(event,ref_table,ref_id,detail) VALUES('assessment_job_created','assessment_jobs',NEW.id,
 json_object('inventory_version_id',NEW.inventory_version_id,'profile_version_id',NEW.profile_version_id));
END;
CREATE TRIGGER audit_assessment_job_state AFTER UPDATE OF state ON assessment_jobs
WHEN NEW.state<>OLD.state BEGIN
 INSERT INTO audit_log(event,ref_table,ref_id,detail) VALUES('assessment_job_state','assessment_jobs',NEW.id,
 json_object('from',OLD.state,'to',NEW.state));
END;
CREATE TRIGGER audit_assessment_job_row AFTER UPDATE OF state ON assessment_job_rows BEGIN
 INSERT INTO audit_log(event,ref_table,ref_id,detail) VALUES('assessment_job_row','assessment_job_rows',NEW.id,
 json_object('job_id',NEW.job_id,'state',NEW.state,'assessment_id',NEW.assessment_id));
END;

CREATE TRIGGER job_terminal BEFORE UPDATE OF state ON assessment_jobs
WHEN (OLD.state='completed' AND NEW.state<>'completed') OR
 (NEW.state='completed' AND EXISTS(SELECT 1 FROM assessment_job_rows WHERE job_id=OLD.id AND state='pending'))
BEGIN SELECT RAISE(ABORT,'tamamlanma durumu tutarsız'); END;
CREATE TRIGGER row_result_versions BEFORE UPDATE OF assessment_id ON assessment_job_rows
WHEN NEW.assessment_id IS NOT NULL AND NOT EXISTS(
 SELECT 1 FROM assessments a JOIN response_sets r ON r.id=a.response_set_id
 JOIN assessment_jobs j ON j.id=NEW.job_id
 WHERE a.id=NEW.assessment_id AND a.profile_version_id=j.profile_version_id
 AND r.inventory_version_id=j.inventory_version_id)
BEGIN SELECT RAISE(ABORT,'iş sonucu sürümleri uyuşmuyor'); END;
