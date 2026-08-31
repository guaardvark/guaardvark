"""scripts/lib/pg_url.sh must read role/db/host/port out of DATABASE_URL, so
start_postgres.sh can verify a non-stock role instead of resetting it."""
import os
import subprocess
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[3] / "scripts" / "lib" / "pg_url.sh"


def parse(url: str):
    script = f'''. "{LIB}"; if pg_url_parse "$1"; then printf '%s\\n%s\\n%s\\n%s\\n%s\\n' "$PG_URL_USER" "$PG_URL_PASS" "$PG_URL_HOST" "$PG_URL_PORT" "$PG_URL_DB"; else echo FAIL; fi'''
    out = subprocess.run(["bash", "-c", script, "bash", url], capture_output=True, text=True, check=True).stdout
    lines = out.rstrip("\n").split("\n")
    return None if lines == ["FAIL"] else tuple(lines)


@pytest.mark.parametrize("url,expected", [
    ("postgresql://roofbrain:s3cret@localhost:5432/roofbrain", ("roofbrain", "s3cret", "localhost", "5432", "roofbrain")),
    ("postgresql://guaardvark:pw@127.0.0.1:5433/guaardvark?sslmode=disable", ("guaardvark", "pw", "127.0.0.1", "5433", "guaardvark")),
    ("postgres://u:p@h/db", ("u", "p", "h", "", "db")),
    ("postgresql+psycopg2://u:p%40x@h:5432/db", ("u", "p%40x", "h", "5432", "db")),
    ("postgresql://u@h:5432/db", ("u", "", "h", "5432", "db")),
])
def test_parses_role_db_host_port(url, expected):
    assert parse(url) == expected


@pytest.mark.parametrize("url", ["mysql://u:p@h/db", "postgresql://h:5432/", "not a url", ""])
def test_rejects_non_postgres_or_incomplete(url):
    assert parse(url) is None


def test_start_postgres_sources_the_library_and_parses_bash():
    root = LIB.parents[2]
    text = (root / "start_postgres.sh").read_text()
    assert 'scripts/lib/pg_url.sh' in text and "refuse_reprovision" in text
    subprocess.run(["bash", "-n", str(root / "start_postgres.sh")], check=True)
