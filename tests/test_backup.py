import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_backup_script_creates_consistent_private_sqlite_copy(tmp_path):
    root = Path(__file__).resolve().parents[1]
    source = tmp_path / "source.db"
    destination = tmp_path / "backups"
    with sqlite3.connect(source) as database:
        database.execute("create table sample (value text)")
        database.execute("insert into sample values ('stored')")

    environment = os.environ.copy()
    environment["APICHECKER_DATABASE_URL"] = f"sqlite:///{source}"
    result = subprocess.run(
        [
            sys.executable,
            str(root / "scripts/backup"),
            "--destination",
            str(destination),
            "--keep",
            "1",
        ],
        cwd=root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    backup = Path(result.stdout.strip()) / "apichecker.db"
    with sqlite3.connect(backup) as database:
        assert database.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert database.execute("select value from sample").fetchone()[0] == "stored"
    assert backup.stat().st_mode & 0o777 == 0o600
