"""normalize と scoring の純ロジックのユニットテスト.

PyYAML/kubectl 不要・ネットワーク不要で動く。標準 unittest のみ:
    python3 -m unittest discover -s tests
"""

import unittest

from quality_agent.config import Context, Policy
from quality_agent.models import Analysis, Metric
from quality_agent.scoring import normalize, score


class TestNormalize(unittest.TestCase):
    def test_linear_endpoints_and_clamp(self):
        self.assertEqual(normalize.linear(100, good=100, bad=0), 100.0)
        self.assertEqual(normalize.linear(0, good=100, bad=0), 0.0)
        self.assertEqual(normalize.linear(50, good=100, bad=0), 50.0)
        # 範囲外はクランプ
        self.assertEqual(normalize.linear(200, good=100, bad=0), 100.0)
        self.assertEqual(normalize.linear(-50, good=100, bad=0), 0.0)

    def test_logarithmic_zero_is_best(self):
        self.assertEqual(normalize.logarithmic(0, good=0, bad=1000), 100.0)
        # bad 到達で 0
        self.assertEqual(normalize.logarithmic(1000, good=0, bad=1000), 0.0)
        # 単調減少
        a = normalize.logarithmic(10, good=0, bad=1000)
        b = normalize.logarithmic(100, good=0, bad=1000)
        self.assertGreater(a, b)

    def test_binary(self):
        self.assertEqual(normalize.binary(True), 100.0)
        self.assertEqual(normalize.binary(False), 0.0)


def _security_metrics(critical=0, high=0, medium=0, low=0, unknown=0, reports=10):
    vals = {
        "critical": critical,
        "high": high,
        "medium": medium,
        "low": low,
        "unknown": unknown,
    }
    metrics = [
        Metric(
            id=f"security.cve.{sev}",
            characteristic="security",
            name=sev,
            value=float(v),
            source="trivy",
        )
        for sev, v in vals.items()
    ]
    metrics.append(
        Metric(
            id="security.scan.report_count",
            characteristic="security",
            name="reports",
            value=float(reports),
            source="trivy",
        )
    )
    return metrics


def _maintainability_metrics(debt_ratio=0.0, duplication=0.0, files=10):
    return [
        Metric(
            id="maintainability.debt_ratio",
            characteristic="maintainability",
            name="debt",
            value=float(debt_ratio),
            unit="percent",
            source="sonarqube",
        ),
        Metric(
            id="maintainability.duplicated_lines_density",
            characteristic="maintainability",
            name="dup",
            value=float(duplication),
            unit="percent",
            source="sonarqube",
        ),
        Metric(
            id="maintainability.files",
            characteristic="maintainability",
            name="files",
            value=float(files),
            source="sonarqube",
        ),
    ]


def _interaction_metrics(sd=80.0, ua=70.0, ar=90.0, evaluated=10):
    metrics = [
        Metric(
            id=mid,
            characteristic="interaction",
            name=mid,
            value=float(v),
            unit="score",
            source="ollama",
        )
        for mid, v in (
            ("interaction.doc.self_descriptiveness", sd),
            ("interaction.doc.user_assistance", ua),
            ("interaction.doc.appropriateness", ar),
        )
    ]
    metrics.append(
        Metric(
            id="interaction.docs.evaluated",
            characteristic="interaction",
            name="evaluated",
            value=float(evaluated),
            source="ollama",
        )
    )
    return metrics


def _forgejo_metrics(
    total=10, closed=6, close_days=3.0, pulls=4, merged=3, merge_days=1.0
):
    metrics = [
        Metric(
            id="forgejo.issues.total",
            characteristic="acceptability",
            name="issues",
            value=float(total),
            source="forgejo",
        ),
        Metric(
            id="forgejo.pulls.total",
            characteristic="acceptability",
            name="pulls",
            value=float(pulls),
            source="forgejo",
        ),
        Metric(
            id="forgejo.pulls.merged",
            characteristic="acceptability",
            name="merged",
            value=float(merged),
            source="forgejo",
        ),
    ]
    if total:
        metrics.append(
            Metric(
                id="acceptability.issue_close_rate",
                characteristic="acceptability",
                name="close rate",
                value=100.0 * closed / total,
                unit="percent",
                source="forgejo",
            )
        )
    if closed:
        metrics.append(
            Metric(
                id="beneficialness.issue_close_days_median",
                characteristic="beneficialness",
                name="close days",
                value=float(close_days),
                unit="days",
                source="forgejo",
            )
        )
    if merged:
        metrics.append(
            Metric(
                id="acceptability.pr_merge_days_median",
                characteristic="acceptability",
                name="merge days",
                value=float(merge_days),
                unit="days",
                source="forgejo",
            )
        )
    return metrics


def _macaron_metrics(passed=2, failed=11):
    evaluated = passed + failed
    metrics = [
        Metric(
            id="security.slsa.checks_passed",
            characteristic="security",
            name="passed",
            value=float(passed),
            source="macaron",
        ),
        Metric(
            id="security.slsa.checks_failed",
            characteristic="security",
            name="failed",
            value=float(failed),
            source="macaron",
        ),
        Metric(
            id="security.slsa.checks_evaluated",
            characteristic="security",
            name="evaluated",
            value=float(evaluated),
            source="macaron",
        ),
    ]
    if evaluated:
        metrics.append(
            Metric(
                id="security.slsa.pass_rate",
                characteristic="security",
                name="pass rate",
                value=100.0 * passed / evaluated,
                unit="percent",
                source="macaron",
            )
        )
    return metrics


def _policy():
    return Policy(
        name="t",
        thresholds={"security": {"good": 0.0, "bad": 100000.0}},
        min_samples=1,
    )


class TestScore(unittest.TestCase):
    def test_clean_repo_scores_high(self):
        analysis = Analysis(
            collected_at="t", target="x", metrics=_security_metrics()
        )
        ctx = Context(context_id="c", weights={"security": 1.0})
        rep = score.score(analysis, ctx, _policy())
        self.assertEqual(rep.overall, 100.0)
        self.assertEqual(rep.characteristics[0].score, 100.0)

    def test_cves_lower_score(self):
        analysis = Analysis(
            collected_at="t",
            target="x",
            metrics=_security_metrics(critical=10, high=50),
        )
        ctx = Context(context_id="c", weights={"security": 1.0})
        rep = score.score(analysis, ctx, _policy())
        self.assertLess(rep.characteristics[0].score, 100.0)
        self.assertGreater(rep.characteristics[0].score, 0.0)

    def test_unimplemented_characteristics_excluded(self):
        analysis = Analysis(
            collected_at="t", target="x", metrics=_security_metrics()
        )
        ctx = Context(
            context_id="c",
            weights={"security": 0.2, "reliability": 0.8},
        )
        rep = score.score(analysis, ctx, _policy())
        # security しか実装していないので総合 = security スコア (重み再正規化)
        self.assertEqual(rep.overall, 100.0)
        self.assertTrue(any("reliability" in n for n in rep.notes))

    def test_maintainability_clean_scores_high(self):
        analysis = Analysis(
            collected_at="t",
            target="x",
            metrics=_security_metrics() + _maintainability_metrics(),
        )
        ctx = Context(
            context_id="c", weights={"security": 0.5, "maintainability": 0.5}
        )
        rep = score.score(analysis, ctx, _policy())
        by_char = {c.characteristic: c for c in rep.characteristics}
        self.assertEqual(by_char["maintainability"].score, 100.0)
        self.assertEqual(rep.overall, 100.0)

    def test_maintainability_debt_lowers_score(self):
        # debt_ratio 25% (bad=50 の中間) + duplication 15% (bad=30 の中間) → 50 点
        analysis = Analysis(
            collected_at="t",
            target="x",
            metrics=_maintainability_metrics(debt_ratio=25.0, duplication=15.0),
        )
        ctx = Context(context_id="c", weights={"maintainability": 1.0})
        rep = score.score(analysis, ctx, _policy())
        by_char = {c.characteristic: c for c in rep.characteristics}
        self.assertEqual(by_char["maintainability"].score, 50.0)

    def test_maintainability_absent_is_noted_and_excluded(self):
        # sonarqube collector がスキップされた場合: 特性自体を出さず note で明示
        analysis = Analysis(
            collected_at="t", target="x", metrics=_security_metrics()
        )
        ctx = Context(
            context_id="c", weights={"security": 0.5, "maintainability": 0.5}
        )
        rep = score.score(analysis, ctx, _policy())
        chars = {c.characteristic for c in rep.characteristics}
        self.assertNotIn("maintainability", chars)
        self.assertTrue(any("maintainability" in n for n in rep.notes))
        # security のみで再正規化される
        self.assertEqual(rep.overall, 100.0)

    def test_lint_characteristics_scored(self):
        # reliability 10/20 -> 50, performance 0 -> 100, safety 5/20 -> 75
        lint_metrics = [
            Metric(
                id="reliability.lint.issues",
                characteristic="reliability",
                name="rel",
                value=10.0,
                source="kubelinter",
            ),
            Metric(
                id="performance.lint.issues",
                characteristic="performance",
                name="perf",
                value=0.0,
                source="kubelinter",
            ),
            Metric(
                id="safety.lint.issues",
                characteristic="safety",
                name="safety",
                value=5.0,
                source="kubelinter",
            ),
            Metric(
                id="lint.files",
                characteristic="reliability",
                name="files",
                value=30.0,
                source="kubelinter",
            ),
        ]
        analysis = Analysis(collected_at="t", target="x", metrics=lint_metrics)
        ctx = Context(
            context_id="c",
            weights={"reliability": 0.5, "performance": 0.5},  # safety は重み 0
        )
        rep = score.score(analysis, ctx, _policy())
        by_char = {c.characteristic: c for c in rep.characteristics}
        self.assertEqual(by_char["reliability"].score, 50.0)
        self.assertEqual(by_char["performance"].score, 100.0)
        self.assertEqual(by_char["safety"].score, 75.0)
        self.assertEqual(by_char["safety"].weight, 0.0)
        # 総合は reliability/performance のみ (safety は weight 0 で除外)
        self.assertEqual(rep.overall, 75.0)

    def test_lint_absent_is_noted(self):
        analysis = Analysis(
            collected_at="t", target="x", metrics=_security_metrics()
        )
        ctx = Context(
            context_id="c", weights={"security": 0.8, "reliability": 0.2}
        )
        rep = score.score(analysis, ctx, _policy())
        chars = {c.characteristic for c in rep.characteristics}
        self.assertNotIn("reliability", chars)
        self.assertTrue(any("kubelinter" in n for n in rep.notes))

    def test_interaction_scored_as_mean_of_subchars(self):
        # (80 + 70 + 90) / 3 = 80 (恒等補間)
        analysis = Analysis(
            collected_at="t", target="x", metrics=_interaction_metrics()
        )
        ctx = Context(context_id="c", weights={"interaction": 1.0})
        rep = score.score(analysis, ctx, _policy())
        by_char = {c.characteristic: c for c in rep.characteristics}
        self.assertEqual(by_char["interaction"].score, 80.0)
        self.assertEqual(rep.overall, 80.0)

    def test_interaction_insufficient_when_no_docs(self):
        analysis = Analysis(
            collected_at="t",
            target="x",
            metrics=_interaction_metrics(evaluated=0),
        )
        ctx = Context(context_id="c", weights={"interaction": 1.0})
        rep = score.score(analysis, ctx, _policy())
        by_char = {c.characteristic: c for c in rep.characteristics}
        self.assertTrue(by_char["interaction"].insufficient_data)
        self.assertEqual(rep.overall, 0.0)

    def test_interaction_absent_is_noted(self):
        # ollama collector がスキップされた場合: 特性を出さず note で明示
        analysis = Analysis(
            collected_at="t", target="x", metrics=_security_metrics()
        )
        ctx = Context(
            context_id="c", weights={"security": 0.8, "interaction": 0.2}
        )
        rep = score.score(analysis, ctx, _policy())
        chars = {c.characteristic for c in rep.characteristics}
        self.assertNotIn("interaction", chars)
        self.assertTrue(any("ollama" in n for n in rep.notes))
        self.assertEqual(rep.overall, 100.0)

    def test_qiu_characteristics_scored(self):
        # beneficialness: クローズ中央 3 日 / bad 30 → 90 点
        # acceptability: クローズ率 60% → 60 点, マージ中央 1 日 / bad 14 → 92.857…
        #   → (60 + 92.9) / 2 ≈ 76.4 点
        analysis = Analysis(
            collected_at="t", target="x", metrics=_forgejo_metrics()
        )
        ctx = Context(
            context_id="c",
            weights={"beneficialness": 0.5, "acceptability": 0.5},
        )
        rep = score.score(analysis, ctx, _policy())
        by_char = {c.characteristic: c for c in rep.characteristics}
        self.assertEqual(by_char["beneficialness"].score, 90.0)
        self.assertAlmostEqual(by_char["acceptability"].score, 76.4, places=1)
        self.assertFalse(by_char["beneficialness"].insufficient_data)

    def test_qiu_insufficient_below_per_characteristic_min(self):
        # 件数 2+1 件 < min_samples_per_characteristic の 5 件 → データ不足扱い
        analysis = Analysis(
            collected_at="t",
            target="x",
            metrics=_forgejo_metrics(total=2, closed=1, pulls=1, merged=1),
        )
        ctx = Context(
            context_id="c",
            weights={"beneficialness": 0.5, "acceptability": 0.5},
        )
        pol = _policy()
        pol.min_samples_per_characteristic = {
            "beneficialness": 5,
            "acceptability": 5,
        }
        rep = score.score(analysis, ctx, pol)
        by_char = {c.characteristic: c for c in rep.characteristics}
        self.assertTrue(by_char["beneficialness"].insufficient_data)
        self.assertTrue(by_char["acceptability"].insufficient_data)
        self.assertEqual(rep.overall, 0.0)

    def test_qiu_absent_is_noted(self):
        # forgejo collector がスキップされた場合: 特性を出さず note で明示
        analysis = Analysis(
            collected_at="t", target="x", metrics=_security_metrics()
        )
        ctx = Context(
            context_id="c",
            weights={"security": 0.8, "beneficialness": 0.1, "acceptability": 0.1},
        )
        rep = score.score(analysis, ctx, _policy())
        chars = {c.characteristic for c in rep.characteristics}
        self.assertNotIn("beneficialness", chars)
        self.assertNotIn("acceptability", chars)
        self.assertEqual(
            sum(1 for n in rep.notes if "forgejo" in n), 2
        )
        self.assertEqual(rep.overall, 100.0)

    def test_security_blends_cve_and_slsa_subscores(self):
        # CVE ゼロ (subscore 100) + SLSA 通過率 50% (subscore 50) → 平均 75
        analysis = Analysis(
            collected_at="t",
            target="x",
            metrics=_security_metrics() + _macaron_metrics(passed=5, failed=5),
        )
        ctx = Context(context_id="c", weights={"security": 1.0})
        rep = score.score(analysis, ctx, _policy())
        by_char = {c.characteristic: c for c in rep.characteristics}
        self.assertEqual(by_char["security"].score, 75.0)
        self.assertIn(
            "security.slsa.pass_rate", by_char["security"].contributing_metrics
        )

    def test_security_slsa_only_when_trivy_skipped(self):
        # trivy collector スキップ時は SLSA 通過率のみでスコア化する
        analysis = Analysis(
            collected_at="t",
            target="x",
            metrics=_macaron_metrics(passed=2, failed=11),
        )
        ctx = Context(context_id="c", weights={"security": 1.0})
        rep = score.score(analysis, ctx, _policy())
        by_char = {c.characteristic: c for c in rep.characteristics}
        self.assertAlmostEqual(by_char["security"].score, 15.4, places=1)
        self.assertFalse(by_char["security"].insufficient_data)

    def test_security_cve_only_unchanged_when_macaron_skipped(self):
        # macaron collector スキップ時は従来どおり CVE サブスコアのみ
        analysis = Analysis(
            collected_at="t", target="x", metrics=_security_metrics()
        )
        ctx = Context(context_id="c", weights={"security": 1.0})
        rep = score.score(analysis, ctx, _policy())
        self.assertEqual(rep.characteristics[0].score, 100.0)

    def test_insufficient_data_excluded_from_overall(self):
        analysis = Analysis(
            collected_at="t",
            target="x",
            metrics=_security_metrics(reports=0),
        )
        ctx = Context(context_id="c", weights={"security": 1.0})
        pol = Policy(
            name="t",
            thresholds={"security": {"good": 0.0, "bad": 100000.0}},
            min_samples=1,
        )
        rep = score.score(analysis, ctx, pol)
        self.assertTrue(rep.characteristics[0].insufficient_data)
        self.assertEqual(rep.overall, 0.0)


if __name__ == "__main__":
    unittest.main()
