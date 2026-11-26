"""Test helpers. All network calls are replaced by recorded fixture responses,
so tests run offline and never spend quota."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FIX = Path(__file__).parent / "fixtures"

from intel.db import connect, migrate  # noqa: E402


class FakeResp:
    def __init__(self, status=200, body=b"", headers=None):
        self.status_code = status
        self.content = body if isinstance(body, bytes) else body.encode()
        self.text = self.content.decode("utf-8", "replace")
        self.headers = headers or {"Content-Type": "text/html; charset=utf-8"}

    def json(self):
        return json.loads(self.content or b"{}")


class FakeWeb:
    """Route URLs (by substring) to responses. Records every call."""
    def __init__(self, routes=None):
        self.routes = routes or {}
        self.calls = []

    def __call__(self, url, params=None, timeout=20, headers=None):
        self.calls.append((url, params))
        for key, resp in self.routes.items():
            if key in url:
                r = resp(url, params) if callable(resp) else resp
                if isinstance(r, Exception):
                    raise r
                return r
        return FakeResp(404, b"not found")


def fixture(name: str) -> bytes:
    return (FIX / name).read_bytes()


import os

if os.environ.get("MI_TEST_TURSO"):
    # Route every database connection through the Turso HTTP adapter + protocol fake.
    import intel.db as _db
    from fake_turso import FakeTurso
    from intel.turso import TursoConnection
    _orig = _db.connect

    def _turso_connect(path=None):
        p = path or _db.db_path()
        return TursoConnection("libsql://test-db.turso.io", "token", transport=FakeTurso(p))
    _db.connect = _turso_connect
    connect = _turso_connect   # noqa: F811


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    yield c
    c.close()


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ("YOUTUBE_API_KEY", "IG_USER_ID", "IG_ACCESS_TOKEN", "ALLOW_PAID_AI", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("OLLAMA_URL", "http://127.0.0.1:9")  # nothing listens here
