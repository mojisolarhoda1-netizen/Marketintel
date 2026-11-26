"""A protocol-faithful stand-in for Turso's HTTP pipeline API, executing on a local SQLite file.

Used to run the whole test suite through intel/turso.py (MI_TEST_TURSO=1), so the online
database path is tested without internet. It speaks the Hrana JSON value encoding exactly.
"""
from __future__ import annotations

import base64
import json
import sqlite3


def _enc(v):
    if v is None:
        return {"type": "null"}
    if isinstance(v, int):
        return {"type": "integer", "value": str(v)}
    if isinstance(v, float):
        return {"type": "float", "value": v}
    if isinstance(v, bytes):
        return {"type": "blob", "base64": base64.b64encode(v).decode()}
    return {"type": "text", "value": v}


def _dec(a):
    t = a["type"]
    if t == "null":
        return None
    if t == "integer":
        assert isinstance(a["value"], str), "integers must be sent as strings"
        return int(a["value"])
    if t == "float":
        assert isinstance(a["value"], (int, float)), "floats must be JSON numbers"
        return float(a["value"])
    if t == "blob":
        return base64.b64decode(a["base64"])
    assert isinstance(a["value"], str)
    return a["value"]


class FakeTurso:
    def __init__(self, path):
        self.path = str(path)
        self.calls = 0

    def __call__(self, body: dict) -> dict:
        body = json.loads(json.dumps(body))           # must be pure JSON, like over HTTP
        self.calls += 1
        conn = sqlite3.connect(self.path, timeout=30)  # a fresh connection per pipeline, like a stream
        results = []
        try:
            for req in body["requests"]:
                if req["type"] == "close":
                    results.append({"type": "ok", "response": {"type": "close"}})
                    continue
                st = req["stmt"]
                try:
                    cur = conn.execute(st["sql"], [_dec(a) for a in st.get("args", [])])
                    rows = cur.fetchall()
                    cols = [{"name": d[0], "decltype": None} for d in (cur.description or [])]
                    conn.commit()
                    results.append({"type": "ok", "response": {"type": "execute", "result": {
                        "cols": cols, "rows": [[_enc(v) for v in r] for r in rows],
                        "affected_row_count": max(cur.rowcount, 0),
                        "last_insert_rowid": str(cur.lastrowid) if cur.lastrowid else None}}})
                except sqlite3.Error as e:
                    results.append({"type": "error", "error": {"message": str(e), "code": "SQLITE_ERROR"}})
        finally:
            conn.close()
        return json.loads(json.dumps({"baton": None, "base_url": None, "results": results}))
