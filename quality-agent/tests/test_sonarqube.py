"""sonarqube collector のユニットテスト (from_json オフライン経路).

ネットワーク不要。api/measures/component のレスポンス形式を模した JSON を
一時ファイルに書いてパースを検証する。
"""

import json
import os
import tempfile
import unittest

from quality_agent.collectors import sonarqube


def _api_response(measures):
    return {
        "component": {
            "key": "local-infra",
            "qualifier": "TRK",
            "measures": measures,
        }
    }


def _write_json(obj) -> str:
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    return path


class TestSonarQubeCollect(unittest.TestCase):
    def test_from_json_maps_measures_to_metrics(self):
        path = _write_json(
            _api_response(
                [
                    {"metric": "ncloc", "value": "1200"},
                    {"metric": "files", "value": "34"},
                    {"metric": "sqale_index", "value": "180"},
                    {"metric": "sqale_debt_ratio", "value": "2.5"},
                    {"metric": "code_smells", "value": "17"},
                    {"metric": "cognitive_complexity", "value": "88"},
                    {"metric": "duplicated_lines_density", "value": "4.2"},
                ]
            )
        )
        try:
            metrics = sonarqube.collect(component="local-infra", from_json=path)
        finally:
            os.unlink(path)

        by_id = {m.id: m for m in metrics}
        self.assertEqual(len(metrics), 7)
        self.assertEqual(by_id["maintainability.debt_ratio"].value, 2.5)
        self.assertEqual(by_id["maintainability.duplicated_lines_density"].value, 4.2)
        self.assertEqual(by_id["maintainability.files"].value, 34.0)
        for m in metrics:
            self.assertEqual(m.characteristic, "maintainability")
            self.assertEqual(m.source, "sonarqube")

    def test_unknown_and_valueless_measures_are_skipped(self):
        path = _write_json(
            _api_response(
                [
                    {"metric": "ncloc", "value": "100"},
                    {"metric": "new_fancy_metric", "value": "1"},  # 要求外
                    {"metric": "sqale_debt_ratio"},  # value なし (新規プロジェクト)
                ]
            )
        )
        try:
            metrics = sonarqube.collect(component="local-infra", from_json=path)
        finally:
            os.unlink(path)
        self.assertEqual([m.id for m in metrics], ["maintainability.ncloc"])

    def test_missing_token_raises_unavailable(self):
        with self.assertRaises(sonarqube.SonarQubeUnavailable):
            sonarqube.collect(component="local-infra", token=None)


if __name__ == "__main__":
    unittest.main()
