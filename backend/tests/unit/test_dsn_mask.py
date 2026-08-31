"""The DSN never reaches a log with its password, truncated or not."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
os.environ.setdefault("GUAARDVARK_MODE", "test")

from backend.config import mask_dsn


def test_mask_dsn_hides_the_password_only():
    assert mask_dsn("postgresql://guaardvark:nFZ6abcdef@localhost:5432/guaardvark") == "postgresql://guaardvark:***@localhost:5432/guaardvark"
    assert mask_dsn("postgresql://u@h/db") == "postgresql://u@h/db"
    assert mask_dsn("") == ""
    assert "nFZ6" not in mask_dsn("postgresql://guaardvark:nFZ6@h/db")
