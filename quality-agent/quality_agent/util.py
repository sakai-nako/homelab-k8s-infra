"""小さな共通ユーティリティ."""

from __future__ import annotations

from datetime import datetime, timezone


def now_iso() -> str:
    """現在時刻を ISO8601 (UTC, 秒精度) で返す."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
