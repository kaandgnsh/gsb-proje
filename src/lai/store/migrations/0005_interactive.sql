-- M6 state is separate from immutable M1–M5 knowledge confidence constraints.
CREATE TABLE learning_gaps (
 id INTEGER PRIMARY KEY, inventory_id INTEGER NOT NULL REFERENCES inventories(id),
 gap_key TEXT NOT NULL, category TEXT NOT NULL,
 severity TEXT NOT NULL CHECK(severity IN ('CRITICAL','HIGH','MEDIUM','LOW')),
 state TEXT NOT NULL CHECK(state IN ('CONFIRMED','STRONGLY_INFERRED','WEAKLY_INFERRED','UNKNOWN','CONTRADICTORY','REJECTED','DEFERRED')),
 payload TEXT NOT NULL CHECK(json_valid(payload)),
 UNIQUE(inventory_id,gap_key)
);
CREATE TABLE learning_answers (
 id INTEGER PRIMARY KEY, gap_id INTEGER NOT NULL REFERENCES learning_gaps(id),
 action TEXT NOT NULL, user_text TEXT NOT NULL, interpretation TEXT NOT NULL CHECK(json_valid(interpretation)),
 created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE dimension_reviews (
 id INTEGER PRIMARY KEY, inventory_id INTEGER NOT NULL REFERENCES inventories(id),
 fit_run_id INTEGER NOT NULL REFERENCES fit_runs(id), dimension_code TEXT NOT NULL,
 detail TEXT NOT NULL CHECK(json_valid(detail))
);
CREATE TRIGGER ro_learning_answers_u BEFORE UPDATE ON learning_answers BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER ro_learning_answers_d BEFORE DELETE ON learning_answers BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER ro_dimension_reviews_u BEFORE UPDATE ON dimension_reviews BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER ro_dimension_reviews_d BEFORE DELETE ON dimension_reviews BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER history_learning_answers AFTER INSERT ON learning_answers BEGIN
 INSERT INTO learning_history(inventory_id,event,ref_table,ref_id,detail)
 SELECT inventory_id,'learning_answer','learning_answers',NEW.id,NEW.interpretation FROM learning_gaps WHERE id=NEW.gap_id;
END;
CREATE TRIGGER history_learning_gaps AFTER UPDATE ON learning_gaps BEGIN
 INSERT INTO learning_history(inventory_id,event,ref_table,ref_id,detail)
 VALUES(NEW.inventory_id,'learning_state','learning_gaps',NEW.id,json_object('from',OLD.state,'to',NEW.state,'old_payload',json(OLD.payload),'new_payload',json(NEW.payload)));
END;

CREATE TRIGGER history_learning_gap_created AFTER INSERT ON learning_gaps BEGIN
 INSERT INTO learning_history(inventory_id,event,ref_table,ref_id,detail)
 VALUES(NEW.inventory_id,'learning_gap_created','learning_gaps',NEW.id,NEW.payload);
END;
CREATE TRIGGER ro_learning_gaps_d BEFORE DELETE ON learning_gaps BEGIN SELECT RAISE(ABORT,'persistent gap'); END;
CREATE TRIGGER audit_dimension_review AFTER INSERT ON dimension_reviews BEGIN
 INSERT INTO audit_log(event,ref_table,ref_id,detail) VALUES('dimension_review','dimension_reviews',NEW.id,NEW.detail);
END;
