"""macaron collector のユニットテスト.

Macaron 本体不要。実機検証 (v0.24.0, ローカル Forgejo clone 解析) で得た
レポート JSON の構造を模したフィクスチャでパースと集計を検証する。
"""

import json
import os
import tempfile
import unittest

from quality_agent.collectors import macaron


def _result(check_id, result_type):
    return {
        "check_id": check_id,
        "check_description": "...",
        "slsa_requirements": [],
        "justification": ["..."],
        "result_type": result_type,
    }


def _report(results, commit="831c561969ae41e2768305818754da062ca4e062"):
    summary = {"PASSED": 0, "FAILED": 0, "SKIPPED": 0, "DISABLED": 0, "UNKNOWN": 0}
    for r in results:
        summary[r["result_type"]] += 1
    return {
        "metadata": {"has_passing_check": summary["PASSED"] > 0},
        "target": {
            "info": {
                "full_name": f"pkg:local_repos/local-infra.git@{commit}",
                "commit_hash": commit,
            },
            "checks": {"summary": summary, "results": results},
        },
    }


def _write_report(obj, dirpath, name="local-infra_git.json"):
    subdir = os.path.join(dirpath, "local_repos", "local-infra_git")
    os.makedirs(subdir, exist_ok=True)
    path = os.path.join(subdir, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    return path


class TestMacaronCollect(unittest.TestCase):
    def test_aggregates_pass_rate_from_evaluated_checks_only(self):
        # PASSED 2 / FAILED 2 / UNKNOWN 1 / SKIPPED 1
        # -> 分母は判定が出た 4 つのみで pass_rate 50%
        report = _report(
            [
                _result("mcn_version_control_system_1", "PASSED"),
                _result("mcn_githubactions_vulnerabilities_1", "PASSED"),
                _result("mcn_provenance_available_1", "FAILED"),
                _result("mcn_build_as_code_1", "FAILED"),
                _result("mcn_license_1", "UNKNOWN"),
                _result("mcn_find_artifact_pipeline_1", "SKIPPED"),
            ]
        )
        with tempfile.TemporaryDirectory() as d:
            path = _write_report(report, d)
            metrics = macaron.collect(report_path=path)
        by_id = {m.id: m for m in metrics}
        self.assertEqual(by_id["security.slsa.checks_passed"].value, 2.0)
        self.assertEqual(by_id["security.slsa.checks_failed"].value, 2.0)
        self.assertEqual(by_id["security.slsa.checks_evaluated"].value, 4.0)
        self.assertEqual(by_id["security.slsa.pass_rate"].value, 50.0)
        self.assertEqual(by_id["security.slsa.pass_rate"].labels["commit"], "831c561969ae")

    def test_per_check_metrics_with_result_label(self):
        report = _report(
            [
                _result("mcn_version_control_system_1", "PASSED"),
                _result("mcn_provenance_available_1", "FAILED"),
                _result("mcn_detect_malicious_metadata_1", "UNKNOWN"),
            ]
        )
        with tempfile.TemporaryDirectory() as d:
            path = _write_report(report, d)
            metrics = macaron.collect(report_path=path)
        by_id = {m.id: m for m in metrics}
        vcs = by_id["slsa.check.mcn_version_control_system_1"]
        self.assertEqual(vcs.value, 1.0)
        self.assertEqual(vcs.labels["result"], "PASSED")
        self.assertEqual(vcs.characteristic, "security")
        self.assertEqual(vcs.subcharacteristic, "resistance")
        prov = by_id["slsa.check.mcn_provenance_available_1"]
        self.assertEqual(prov.value, 0.0)
        self.assertEqual(prov.labels["result"], "FAILED")
        # 判定不能も value 0 だが result ラベルで FAILED と区別できる
        self.assertEqual(
            by_id["slsa.check.mcn_detect_malicious_metadata_1"].labels["result"],
            "UNKNOWN",
        )

    def test_directory_search_finds_report_and_skips_dependencies(self):
        # レポート名は origin URL 由来で固定できないためディレクトリ探索が本経路。
        # 同居する dependencies.json は集計対象にしない。
        report = _report([_result("mcn_version_control_system_1", "PASSED")])
        with tempfile.TemporaryDirectory() as d:
            _write_report(report, d)
            _write_report({"analyzed_deps": 0}, d, name="dependencies.json")
            metrics = macaron.collect(report_path=d)
        by_id = {m.id: m for m in metrics}
        self.assertEqual(by_id["security.slsa.pass_rate"].value, 100.0)

    def test_no_pass_rate_when_nothing_evaluated(self):
        # 全 check が UNKNOWN/SKIPPED なら pass_rate を出さない (0% と誤読させない)
        report = _report([_result("mcn_license_1", "UNKNOWN")])
        with tempfile.TemporaryDirectory() as d:
            path = _write_report(report, d)
            metrics = macaron.collect(report_path=path)
        by_id = {m.id: m for m in metrics}
        self.assertNotIn("security.slsa.pass_rate", by_id)
        self.assertEqual(by_id["security.slsa.checks_evaluated"].value, 0.0)

    def test_missing_report_raises_unavailable(self):
        with self.assertRaises(macaron.MacaronUnavailable):
            macaron.collect(report_path="/no/such/path")

    def test_empty_directory_raises_unavailable(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(macaron.MacaronUnavailable):
                macaron.collect(report_path=d)

    def test_multiple_reports_raise_unavailable(self):
        # 解析対象は単一リポジトリ想定。複数レポートは構成ミスとして明示的に拒否
        report = _report([_result("mcn_version_control_system_1", "PASSED")])
        with tempfile.TemporaryDirectory() as d:
            _write_report(report, d, name="a.json")
            _write_report(report, d, name="b.json")
            with self.assertRaises(macaron.MacaronUnavailable):
                macaron.collect(report_path=d)

    def test_unexpected_schema_raises_unavailable(self):
        # 想定外スキーマを「check ゼロ」と誤読しない
        with tempfile.TemporaryDirectory() as d:
            path = _write_report({"something": "else"}, d)
            with self.assertRaises(macaron.MacaronUnavailable):
                macaron.collect(report_path=path)

    def test_unspecified_path_raises_unavailable(self):
        with self.assertRaises(macaron.MacaronUnavailable):
            macaron.collect(report_path=None)


if __name__ == "__main__":
    unittest.main()
