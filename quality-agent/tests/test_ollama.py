"""ollama collector のユニットテスト (オフライン経路のみ).

Ollama 本体は不要。from_json の集計、文書探索、モデル出力パースを検証する。
HTTP を叩く経路は実機スモークテスト (runbook 参照) で確認する。
"""

import json
import os
import tempfile
import unittest

from quality_agent.collectors import ollama


def _write_json(obj) -> str:
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    return path


def _collect_from(obj):
    path = _write_json(obj)
    try:
        return ollama.collect(from_json=path)
    finally:
        os.unlink(path)


def _evaluation(doc, sd, ua, ar):
    return {
        "doc": doc,
        "self_descriptiveness": sd,
        "user_assistance": ua,
        "appropriateness_recognizability": ar,
    }


class TestOllamaCollectFromJson(unittest.TestCase):
    def test_aggregates_subcharacteristic_means(self):
        metrics = _collect_from(
            {
                "model": "qwen3:14b",
                "evaluations": [
                    _evaluation("README.md", 80, 70, 90),
                    _evaluation("docs/runbooks/x.md", 60, 50, 70),
                ],
            }
        )
        by_id = {m.id: m for m in metrics}
        self.assertEqual(by_id["interaction.doc.self_descriptiveness"].value, 70.0)
        self.assertEqual(by_id["interaction.doc.user_assistance"].value, 60.0)
        self.assertEqual(by_id["interaction.doc.appropriateness"].value, 80.0)
        self.assertEqual(by_id["interaction.docs.evaluated"].value, 2.0)

    def test_min_score_points_worst_doc(self):
        metrics = _collect_from(
            {
                "evaluations": [
                    _evaluation("README.md", 80, 80, 80),
                    _evaluation("docs/bad.md", 30, 40, 50),
                ],
            }
        )
        by_id = {m.id: m for m in metrics}
        worst = by_id["interaction.doc.min_score"]
        self.assertEqual(worst.value, 40.0)
        self.assertEqual(worst.labels["doc"], "docs/bad.md")

    def test_model_recorded_in_labels(self):
        # モデル差し替えで採点基準線がズレるため全メトリクスに記録する
        metrics = _collect_from(
            {
                "model": "gemma2:9b",
                "evaluations": [_evaluation("README.md", 80, 80, 80)],
            }
        )
        for m in metrics:
            self.assertEqual(m.labels.get("model"), "gemma2:9b")

    def test_per_doc_metric_emitted(self):
        metrics = _collect_from(
            {"evaluations": [_evaluation("docs/a.md", 90, 60, 90)]}
        )
        by_id = {m.id: m for m in metrics}
        self.assertEqual(by_id["doc.score.docs/a.md"].value, 80.0)
        self.assertEqual(by_id["doc.score.docs/a.md"].labels["doc"], "docs/a.md")

    def test_empty_evaluations_raises_unavailable(self):
        path = _write_json({"evaluations": []})
        try:
            with self.assertRaises(ollama.OllamaUnavailable):
                ollama.collect(from_json=path)
        finally:
            os.unlink(path)


class TestOllamaCollectGuards(unittest.TestCase):
    def test_missing_base_url_raises_unavailable(self):
        with self.assertRaises(ollama.OllamaUnavailable):
            ollama.collect(docs_path=".", base_url=None)

    def test_missing_docs_path_raises_unavailable(self):
        with self.assertRaises(ollama.OllamaUnavailable):
            ollama.collect(docs_path="/no/such/dir", base_url="http://x:11434")


class TestDiscoverDocs(unittest.TestCase):
    def test_finds_readme_and_docs_markdown_deterministically(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "docs", "runbooks"))
            for rel in (
                "README.md",
                os.path.join("docs", "b.md"),
                os.path.join("docs", "a.md"),
                os.path.join("docs", "runbooks", "r.md"),
                os.path.join("docs", "ignored.txt"),
            ):
                with open(os.path.join(root, rel), "w", encoding="utf-8") as f:
                    f.write("x")
            rels = ollama._discover_docs(root)
        # README 先頭、以下パス昇順。.txt は含まない (区切りは OS 依存)
        self.assertEqual(
            [r.replace(os.sep, "/") for r in rels],
            ["README.md", "docs/a.md", "docs/b.md", "docs/runbooks/r.md"],
        )

    def test_no_markdown_raises_unavailable(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ollama.OllamaUnavailable):
                ollama._discover_docs(root)


class TestParseEval(unittest.TestCase):
    def test_valid_json_parsed_and_clamped(self):
        out = ollama._parse_eval(
            json.dumps(
                {
                    "self_descriptiveness": 150,
                    "user_assistance": -5,
                    "appropriateness_recognizability": 70,
                    "comment": "ok",
                }
            )
        )
        self.assertEqual(out["self_descriptiveness"], 100.0)
        self.assertEqual(out["user_assistance"], 0.0)
        self.assertEqual(out["appropriateness_recognizability"], 70.0)

    def test_non_json_raises_value_error(self):
        with self.assertRaises(ValueError):
            ollama._parse_eval("<think>考え中…</think>")

    def test_missing_key_raises_value_error(self):
        with self.assertRaises(ValueError):
            ollama._parse_eval(json.dumps({"self_descriptiveness": 80}))


if __name__ == "__main__":
    unittest.main()
