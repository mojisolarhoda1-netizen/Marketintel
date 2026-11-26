"""Settings from environment variables, with an optional .env file.

No third-party dotenv package needed: a tiny parser keeps the install free of extras.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path | None = None) -> None:
    p = path or ROOT / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def env_bool(key: str, default: bool = False) -> bool:
    v = env(key)
    return default if not v else v.lower() in {"1", "true", "yes", "on"}


def env_int(key: str, default: int) -> int:
    try:
        return int(env(key) or default)
    except ValueError:
        return default


def on_vercel() -> bool:
    return bool(os.environ.get("VERCEL"))


USER_AGENT = "MarketIntelBot/0.1 (local research prototype; respects robots.txt)"
