import sqlite3

import pytest
import db


def test_delete_is_scoped_and_atomic(monkeypatch):
    connection = sqlite3.connect(":memory:")
    connection.executescript(db.SCHEMA)
    connection.execute("CREATE TABLE shortlist (campaign_id TEXT, creator_id TEXT)")
    monkeypatch.setattr(db, "_conn", connection)
    for cid in ("delete", "keep"):
        connection.execute("INSERT INTO campaigns VALUES (?, '', '{}', '')", (cid,))
        for table in ("features", "evidence", "rankings", "discoveries", "visual_tags", "run_stats", "shortlist"):
            connection.execute(f"INSERT INTO {table} (campaign_id) VALUES (?)", (cid,))
    connection.execute("INSERT INTO creators (id) VALUES ('shared')")
    connection.commit()
    connection.execute("CREATE TRIGGER reject_delete BEFORE DELETE ON campaigns BEGIN SELECT RAISE(ABORT, 'test'); END")
    with pytest.raises(sqlite3.IntegrityError):
        db.delete_campaign("delete")
    assert connection.execute("SELECT COUNT(*) FROM features").fetchone()[0] == 2
    connection.execute("DROP TRIGGER reject_delete")
    db.delete_campaign("delete")
    for table in ("features", "evidence", "rankings", "discoveries", "visual_tags", "run_stats", "shortlist"):
        assert connection.execute(f"SELECT campaign_id FROM {table}").fetchall() == [("keep",)]
    assert connection.execute("SELECT id FROM campaigns").fetchall() == [("keep",)]
    assert connection.execute("SELECT id FROM creators").fetchall() == [("shared",)]
    connection.close()
