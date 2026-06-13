"""ScoreReport を PostgreSQL (pg-main の quality DB) に時系列保存する reporter.

psycopg は --to-postgres 経路でのみ必要な *任意依存*. core / stdout 経路は
標準ライブラリ + PyYAML のみで動かす方針なので、psycopg は関数内で lazy import する。

接続は 12-factor に従い環境変数で与える:
  - QUALITY_AGENT_DSN があればそれを libpq DSN/URI として使う
  - 無ければ標準の PG* 環境変数 (PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE) に委ねる
"""

from __future__ import annotations

import os
from importlib import resources

from ..models import ScoreReport


def _load_schema_statements() -> list[str]:
    """schema.sql を ; 区切りの文に分解する.

    dollar-quote (``$$ ... $$``) の内側の ; では分割しない (GRANT 用 DO ブロック対応)。
    タグ付き dollar-quote (``$tag$``) と文中 ``--`` コメントは非対応なので
    schema.sql では使わないこと。
    """
    sql = (
        resources.files("quality_agent.report")
        .joinpath("schema.sql")
        .read_text(encoding="utf-8")
    )
    # 行頭コメントを先に落とす (コメント内の ; や $$ を誤検出しないため)
    text = "\n".join(
        line for line in sql.splitlines() if not line.strip().startswith("--")
    )

    statements: list[str] = []
    buf: list[str] = []
    in_dollar = False
    i = 0
    while i < len(text):
        if text.startswith("$$", i):
            in_dollar = not in_dollar
            buf.append("$$")
            i += 2
            continue
        ch = text[i]
        if ch == ";" and not in_dollar:
            body = "".join(buf).strip()
            if body:
                statements.append(body)
            buf = []
        else:
            buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def _connect(dsn: str | None):
    import psycopg  # lazy: 任意依存

    conn_str = dsn or os.environ.get("QUALITY_AGENT_DSN")
    if conn_str:
        return psycopg.connect(conn_str)
    return psycopg.connect()  # PG* 環境変数に委ねる


def ensure_schema(conn) -> None:
    """スキーマを冪等適用する."""
    with conn.cursor() as cur:
        for stmt in _load_schema_statements():
            cur.execute(stmt)
    conn.commit()


def write(
    report: ScoreReport,
    *,
    conn=None,
    dsn: str | None = None,
    create_schema: bool = True,
) -> int:
    """report を保存し run_id を返す.

    conn を渡せばそれを使う (テスト時の DI / 接続再利用)。渡さなければ環境変数から
    接続を開き、関数内で close する。
    """
    own = conn is None
    if own:
        conn = _connect(dsn)
    try:
        if create_schema:
            ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO quality_runs (scored_at, target, context_id, overall) "
                "VALUES (%s, %s, %s, %s) RETURNING run_id",
                (
                    report.scored_at,
                    report.target,
                    report.context_id,
                    report.overall,
                ),
            )
            run_id = cur.fetchone()[0]
            for c in report.characteristics:
                cur.execute(
                    "INSERT INTO quality_characteristic_scores "
                    "(run_id, characteristic, score, weight, insufficient_data) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (run_id, c.characteristic, c.score, c.weight, c.insufficient_data),
                )
        conn.commit()
        return int(run_id)
    finally:
        if own:
            conn.close()
