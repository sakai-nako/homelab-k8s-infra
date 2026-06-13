"""forgejo collector のユニットテスト (from_json オフライン経路).

ネットワーク不要。Forgejo API (/repos/{owner}/{repo}/issues, /pulls) のレスポンス
形式を模した JSON を一時ファイルに書いて集計を検証する。時刻依存の滞留 Issue
判定は now を注入して決定的にする。
"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone

from quality_agent.collectors import forgejo

NOW = datetime(2026, 6, 11, 0, 0, 0, tzinfo=timezone.utc)


def _issue(state, created, closed=None):
    return {"state": state, "created_at": created, "closed_at": closed}


def _pull(state, created, merged_at=None):
    return {
        "state": state,
        "merged": merged_at is not None,
        "created_at": created,
        "merged_at": merged_at,
    }


def _write_json(obj) -> str:
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    return path


def _collect(payload):
    path = _write_json(payload)
    try:
        return {
            m.id: m
            for m in forgejo.collect(repo="sakai/local-infra", from_json=path, now=NOW)
        }
    finally:
        os.unlink(path)


class TestForgejoCollect(unittest.TestCase):
    def test_counts_rates_and_medians(self):
        by_id = _collect(
            {
                "issues": [
                    # クローズ済み: 2 日と 10 日 → 中央値 6.0
                    _issue("closed", "2026-05-01T00:00:00Z", "2026-05-03T00:00:00Z"),
                    _issue("closed", "2026-05-01T00:00:00Z", "2026-05-11T00:00:00Z"),
                    # 未解決: 100 日経過 (滞留) と 5 日経過
                    _issue("open", "2026-03-03T00:00:00Z"),
                    _issue("open", "2026-06-06T00:00:00Z"),
                ],
                "pulls": [
                    _pull("closed", "2026-05-01T00:00:00Z", "2026-05-02T00:00:00Z"),
                    _pull("open", "2026-06-01T00:00:00Z"),
                ],
            }
        )
        self.assertEqual(by_id["forgejo.issues.total"].value, 4.0)
        self.assertEqual(by_id["forgejo.issues.open"].value, 2.0)
        self.assertEqual(by_id["forgejo.issues.closed"].value, 2.0)
        self.assertEqual(by_id["forgejo.pulls.total"].value, 2.0)
        self.assertEqual(by_id["forgejo.pulls.merged"].value, 1.0)
        self.assertEqual(by_id["acceptability.issue_close_rate"].value, 50.0)
        self.assertEqual(by_id["beneficialness.issue_close_days_median"].value, 6.0)
        self.assertEqual(by_id["beneficialness.issue_stale_open"].value, 1.0)
        self.assertEqual(by_id["acceptability.pr_merge_days_median"].value, 1.0)
        for m in by_id.values():
            self.assertEqual(m.source, "forgejo")
            self.assertEqual(m.labels["repo"], "sakai/local-infra")

    def test_empty_repo_emits_counts_only(self):
        by_id = _collect({"issues": [], "pulls": []})
        self.assertEqual(by_id["forgejo.issues.total"].value, 0.0)
        self.assertEqual(by_id["beneficialness.issue_stale_open"].value, 0.0)
        # 比率・中央値系は分母ゼロのため出さない
        self.assertNotIn("acceptability.issue_close_rate", by_id)
        self.assertNotIn("beneficialness.issue_close_days_median", by_id)
        self.assertNotIn("acceptability.pr_merge_days_median", by_id)

    def test_timestamp_offsets_and_sentinels(self):
        by_id = _collect(
            {
                "issues": [
                    # +09:00 オフセット表記でも閉鎖まで 1 日と解釈される
                    _issue(
                        "closed",
                        "2026-05-01T09:00:00+09:00",
                        "2026-05-02T09:00:00+09:00",
                    ),
                    # 0001 年 sentinel / null は「日時なし」として中央値から除外
                    _issue("closed", "2026-05-01T00:00:00Z", "0001-01-01T00:00:00Z"),
                    _issue("closed", None, "2026-05-02T00:00:00Z"),
                ],
                "pulls": [],
            }
        )
        self.assertEqual(by_id["forgejo.issues.closed"].value, 3.0)
        self.assertEqual(by_id["beneficialness.issue_close_days_median"].value, 1.0)

    def test_missing_token_raises_unavailable(self):
        with self.assertRaises(forgejo.ForgejoUnavailable):
            forgejo.collect(repo="sakai/local-infra", token=None)


if __name__ == "__main__":
    unittest.main()
