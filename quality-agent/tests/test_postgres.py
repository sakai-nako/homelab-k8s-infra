"""report/postgres.py のロジックを fake connection で検証する.

実 psycopg / DB は不要。SQL とパラメータが正しく組まれるかと、schema.sql が
パースできるかを確認する (実エンジンに対する DDL 検証は pg-main pod 内の psql で別途実施)。
"""

import unittest

from quality_agent.models import CharacteristicScore, ScoreReport
from quality_agent.report import postgres


class _FakeCursor:
    def __init__(self, store):
        self.store = store

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.store["calls"].append((sql, params))

    def fetchone(self):
        return (42,)  # RETURNING run_id


class _FakeConn:
    def __init__(self):
        self.store = {"calls": [], "commits": 0}

    def cursor(self):
        return _FakeCursor(self.store)

    def commit(self):
        self.store["commits"] += 1


def _report():
    return ScoreReport(
        scored_at="2026-05-30T10:00:00+00:00",
        target="local-infra",
        context_id="home-monitoring-app",
        overall=17.5,
        characteristics=[
            CharacteristicScore(
                characteristic="security",
                score=17.5,
                weight=0.20,
                contributing_metrics=["security.cve.critical"],
                insufficient_data=False,
            )
        ],
        notes=[],
    )


class TestPostgresReporter(unittest.TestCase):
    def test_write_issues_run_and_characteristic_inserts(self):
        conn = _FakeConn()
        run_id = postgres.write(_report(), conn=conn, create_schema=False)

        self.assertEqual(run_id, 42)
        calls = conn.store["calls"]
        # quality_runs への INSERT + 特性 1 件の INSERT = 2 回
        self.assertEqual(len(calls), 2)

        run_sql, run_params = calls[0]
        self.assertIn("INSERT INTO quality_runs", run_sql)
        self.assertEqual(
            run_params,
            ("2026-05-30T10:00:00+00:00", "local-infra", "home-monitoring-app", 17.5),
        )

        char_sql, char_params = calls[1]
        self.assertIn("quality_characteristic_scores", char_sql)
        self.assertEqual(char_params, (42, "security", 17.5, 0.20, False))

        # commit されていること
        self.assertEqual(conn.store["commits"], 1)

    def test_schema_statements_parse(self):
        stmts = postgres._load_schema_statements()
        joined = " ".join(stmts).lower()
        self.assertTrue(any("create table" in s.lower() for s in stmts))
        self.assertIn("quality_runs", joined)
        self.assertIn("quality_characteristic_scores", joined)
        # コメント行は除去されている
        self.assertFalse(any(s.strip().startswith("--") for s in stmts))

    def test_schema_do_block_is_single_statement(self):
        """GRANT 用 DO ブロックが内側の ; で分割されないこと."""
        stmts = postgres._load_schema_statements()
        do_stmts = [s for s in stmts if s.lower().startswith("do")]
        self.assertEqual(len(do_stmts), 1)
        do_stmt = do_stmts[0]
        # $$ が対で残り、BEGIN〜END の中身 (複数の GRANT 文) が 1 文に収まっている
        self.assertEqual(do_stmt.count("$$"), 2)
        self.assertIn("quality_ro", do_stmt)
        self.assertIn("GRANT SELECT ON ALL TABLES", do_stmt)
        self.assertIn("ALTER DEFAULT PRIVILEGES", do_stmt)
        # DO ブロック以外の文には ; の残骸が無い
        for s in stmts:
            if s is not do_stmt:
                self.assertNotIn(";", s)


if __name__ == "__main__":
    unittest.main()
