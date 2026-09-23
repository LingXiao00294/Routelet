from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

import pytest

from routelet.db import CallStore
from scripts.prune_calls import main


async def test_prune_calls_previews_before_deleting_and_keeps_recent_rows(
    tmp_path, capsys
):
    db_path = tmp_path / "calls.db"
    store = CallStore(str(db_path))
    await store.init()
    try:
        old_id = await store.record(virtual_model="old", status="success")
        recent_id = await store.record(virtual_model="recent", status="success")
        old_timestamp = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
        await store.conn.execute(
            "UPDATE calls SET timestamp = ? WHERE id = ?", (old_timestamp, old_id)
        )
        await store.conn.commit()
    finally:
        await store.close()

    args = ["--db", str(db_path), "--older-than-days", "30"]
    assert main(args) == 0
    assert "预览：1 条" in capsys.readouterr().out
    with closing(sqlite3.connect(db_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 2

    assert main([*args, "--apply", "--vacuum"]) == 0
    output = capsys.readouterr().out
    assert "已清理 1 条" in output
    assert "VACUUM 已完成" in output
    with closing(sqlite3.connect(db_path)) as conn:
        ids = {row[0] for row in conn.execute("SELECT id FROM calls")}
    assert ids == {recent_id}


def test_prune_calls_rejects_missing_database_and_invalid_options(tmp_path):
    db_path = tmp_path / "missing.db"
    with pytest.raises(SystemExit):
        main(["--db", str(db_path), "--older-than-days", "30", "--apply"])
    assert not db_path.exists()
    with pytest.raises(SystemExit):
        main(["--older-than-days", "0"])
    with pytest.raises(SystemExit):
        main(["--older-than-days", "30", "--vacuum"])
