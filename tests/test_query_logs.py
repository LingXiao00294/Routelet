from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from routelet.db import CallStore


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "query_logs.py"


def _run_query_script(home: Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


async def test_query_logs_uses_default_data_directory_from_another_cwd(tmp_path):
    home = tmp_path / "home"
    working = tmp_path / "working"
    working.mkdir()
    db_path = home / ".routelet" / "calls.db"
    store = CallStore(str(db_path))
    await store.init()
    await store.record(virtual_model="router", status="success")
    await store.close()

    result = _run_query_script(home, working)

    assert result.returncode == 0, result.stderr
    assert "总计: 1 条" in result.stdout
    assert not (working / "calls.db").exists()


def test_query_logs_does_not_create_missing_database(tmp_path):
    home = tmp_path / "home"
    working = tmp_path / "working"
    working.mkdir()

    result = _run_query_script(home, working)

    assert result.returncode != 0
    assert "数据库文件不存在" in result.stderr
    assert not (home / ".routelet" / "calls.db").exists()
    assert not (working / "calls.db").exists()
