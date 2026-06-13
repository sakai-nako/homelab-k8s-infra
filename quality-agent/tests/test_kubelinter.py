"""kubelinter collector のユニットテスト (from_json オフライン経路).

kube-linter バイナリ不要。`lint --format json` の出力形式を模した JSON を
一時ファイルに書いてパースと特性マッピングを検証する。
"""

import json
import os
import tempfile
import unittest

from quality_agent.collectors import kubelinter


def _report(check, message="m", file_path="manifests/x.yaml"):
    return {
        "Diagnostic": {"Message": message},
        "Check": check,
        "Remediation": "...",
        "Object": {
            "Metadata": {"FilePath": file_path},
            "K8sObject": {
                "Namespace": "quality-agent",
                "Name": "x",
                "GroupVersionKind": {
                    "Group": "batch",
                    "Version": "v1",
                    "Kind": "CronJob",
                },
            },
        },
    }


def _write_json(obj) -> str:
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    return path


def _collect_from(obj):
    path = _write_json(obj)
    try:
        return kubelinter.collect(from_json=path)
    finally:
        os.unlink(path)


class TestKubeLinterCollect(unittest.TestCase):
    def test_checks_map_to_characteristics(self):
        metrics = _collect_from(
            {
                "Checks": None,
                "Reports": [
                    _report("no-liveness-probe"),
                    _report("no-liveness-probe"),
                    _report("unset-memory-requirements"),
                    _report("unset-cpu-requirements"),
                    _report("run-as-non-root"),
                    _report("brand-new-future-check"),  # 未知 -> reliability に倒す
                ],
            }
        )
        by_id = {m.id: m for m in metrics}
        self.assertEqual(by_id["reliability.lint.issues"].value, 3.0)
        self.assertEqual(by_id["performance.lint.issues"].value, 2.0)
        self.assertEqual(by_id["safety.lint.issues"].value, 1.0)
        self.assertEqual(by_id["lint.check.no-liveness-probe"].value, 2.0)
        self.assertEqual(
            by_id["lint.check.unset-memory-requirements"].characteristic,
            "performance",
        )
        self.assertEqual(
            by_id["lint.check.run-as-non-root"].characteristic, "safety"
        )

    def test_null_reports_means_zero_issues(self):
        # Go の nil スライス: 指摘ゼロは Reports: null で返る
        metrics = _collect_from({"Checks": None, "Reports": None})
        by_id = {m.id: m for m in metrics}
        self.assertEqual(by_id["reliability.lint.issues"].value, 0.0)
        self.assertEqual(by_id["performance.lint.issues"].value, 0.0)
        self.assertEqual(by_id["safety.lint.issues"].value, 0.0)

    def test_missing_reports_key_raises_unavailable(self):
        # 想定外スキーマを「指摘ゼロ」と誤読しない
        path = _write_json({"something": "else"})
        try:
            with self.assertRaises(kubelinter.KubeLinterUnavailable):
                kubelinter.collect(from_json=path)
        finally:
            os.unlink(path)

    def test_missing_target_path_raises_unavailable(self):
        with self.assertRaises(kubelinter.KubeLinterUnavailable):
            kubelinter.collect(path="/no/such/dir")


if __name__ == "__main__":
    unittest.main()
