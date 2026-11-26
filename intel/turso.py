"""Turso (hosted SQLite) over its HTTP API, behaving like Python's sqlite3 connection.

Why: Vercel's disk is read-only, so the database must live online. Turso speaks the same
SQL as the local SQLite file, so the whole app runs unchanged. No extra driver is needed —
just HTTPS requests (Hrana-over-HTTP "pipeline" API: POST {url}/v2/pipeline).

Each statement is sent with `PRAGMA foreign_keys=ON` so ON DELETE CASCADE works as locally.
Statements auto-commit; commit()/rollback() are accepted for compatibility.
"""
from __future__ import annotations

import base64
import re
from typing import Any, Callable

import requests


class TursoError(Exception):
    pass


class Row:
    """Like sqlite3.Row: row["col"], row[0], dict(row), iteration over values, slicing."""
    __slots__ = ("_cols", "_vals")

    def __init__(self, cols: list[str], vals: list[Any]):
        self._cols, self._vals = cols, vals

    def keys(self):
        return list(self._cols)

    def __getitem__(self, k):
        if isinstance(k, (int, slice)):
            return self._vals[k]
        try:
            return self._vals[self._cols.index(k)]
        except ValueError:
            raise IndexError(f"No item with that key: {k}")

    def __iter__(self):
        return iter(self._vals)

    def __len__(self):
        return len(self._vals)

    def __eq__(self, other):
        return list(self) == list(other)

    def __repr__(self):
        return f"Row({dict(zip(self._cols, self._vals))})"


def encode(v) -> dict:
    if v is None:
        return {"type": "null"}
    if isinstance(v, bool):
        return {"type": "integer", "value": str(int(v))}
    if isinstance(v, int):
        return {"type": "integer", "value": str(v)}
    if isinstance(v, float):
        return {"type": "float", "value": v}
    if isinstance(v, (bytes, bytearray, memoryview)):
        return {"type": "blob", "base64": base64.b64encode(bytes(v)).decode()}
    return {"type": "text", "value": str(v)}


def decode(v: dict):
    t = v.get("type")
    if t == "null":
        return None
    if t == "integer":
        return int(v["value"])
    if t == "float":
        return float(v["value"])
    if t == "blob":
        return base64.b64decode(v.get("base64", ""))
    return v.get("value")


class Cursor:
    def __init__(self, cols, rows, rowcount, lastrowid):
        self.description = [(c,) for c in cols] if cols else None
        self._rows = [Row(cols, tuple(r)) for r in rows]
        self.rowcount = rowcount
        self.lastrowid = lastrowid
        self._i = 0

    def fetchone(self):
        if self._i >= len(self._rows):
            return None
        r = self._rows[self._i]
        self._i += 1
        return r

    def fetchall(self):
        out = self._rows[self._i:]
        self._i = len(self._rows)
        return out

    def __iter__(self):
        return iter(self.fetchall())


_COMMENT = re.compile(r"--[^\n]*")


def split_script(sql: str) -> list[str]:
    """Split a migration file into statements (comments removed; our SQL has no ';' in strings)."""
    return [s.strip() for s in _COMMENT.sub("", sql).split(";") if s.strip()]


class TursoConnection:
    row_factory = None  # accepted for sqlite3 compatibility

    def __init__(self, url: str, token: str, transport: Callable[[dict], dict] | None = None, timeout: int = 20):
        if url.startswith(("libsql://", "turso://")):
            url = "https://" + url.split("://", 1)[1]
        self.url = url.rstrip("/") + "/v2/pipeline"
        self.token = token
        self.timeout = timeout
        self._session = requests.Session()
        self._transport = transport or self._http

    # -- transport --
    def _http(self, body: dict) -> dict:
        try:
            r = self._session.post(self.url, json=body, timeout=self.timeout,
                                   headers={"Authorization": f"Bearer {self.token}"})
        except requests.RequestException as e:
            raise TursoError(f"database unreachable: {e.__class__.__name__}") from e
        if r.status_code == 401:
            raise TursoError("database refused the token (check TURSO_AUTH_TOKEN)")
        if r.status_code != 200:
            raise TursoError(f"database HTTP {r.status_code}: {r.text[:200]}")
        return r.json()

    def _run(self, stmts: list[tuple[str, tuple]]) -> list[dict]:
        reqs = [{"type": "execute", "stmt": {"sql": "PRAGMA foreign_keys=ON"}}]
        for sql, params in stmts:
            reqs.append({"type": "execute", "stmt": {"sql": sql, "args": [encode(p) for p in params]}})
        reqs.append({"type": "close"})
        res = self._transport({"baton": None, "requests": reqs})["results"]
        out = []
        for item, (sql, _) in zip(res[1:-1], stmts):
            if item.get("type") == "error":
                msg = (item.get("error") or {}).get("message", "unknown error")
                raise TursoError(f"{msg} — in: {sql[:120]}")
            out.append(item["response"]["result"])
        return out

    # -- sqlite3-like API --
    def execute(self, sql: str, params=()) -> Cursor:
        if sql.strip().upper().startswith("PRAGMA JOURNAL_MODE"):
            return Cursor([], [], -1, None)  # not applicable to a remote database
        res = self._run([(sql, tuple(params))])[0]
        cols = [c.get("name") for c in res.get("cols", [])]
        rows = [[decode(v) for v in row] for row in res.get("rows", [])]
        lid = res.get("last_insert_rowid")
        return Cursor(cols, rows, res.get("affected_row_count", 0), int(lid) if lid not in (None, "") else None)

    def executescript(self, sql: str) -> None:
        stmts = [(s, ()) for s in split_script(sql)]
        for i in range(0, len(stmts), 40):
            self._run(stmts[i:i + 40])

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        self._session.close()

    def backup(self, _target):
        raise TursoError("Backups of the online database are made in the Turso dashboard (point-in-time restore).")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False
