"""Analysis -> ScoreReport. 特性別スコアと CoU 重み付き総合スコアを算出する."""

from __future__ import annotations

from ..config import Context, Policy
from ..models import Analysis, CharacteristicScore, Metric, ScoreReport
from ..util import now_iso
from . import normalize

# スコアラを実装済みの特性
IMPLEMENTED = {
    "security",
    "maintainability",
    "reliability",
    "performance",
    "safety",
    "interaction",
    "beneficialness",
    "acceptability",
}

# しきい値が policy に無い場合のフォールバック。maintainability の bad は
# SonarQube レーティング境界に合わせる (debt_ratio: >50% = E, duplication は慣例値)。
# lint 系は自作 manifests の規模 (数十オブジェクト) 前提の暫定値で、policy 側で調整する。
_DEFAULT_THRESHOLDS = {
    "security": {"good": 0.0, "bad": 500.0},
    # Macaron check 通過率 (0-100, 高いほど良い) をそのまま使う恒等補間
    "security.slsa": {"good": 100.0, "bad": 0.0},
    "maintainability.debt_ratio": {"good": 0.0, "bad": 50.0},
    "maintainability.duplication": {"good": 0.0, "bad": 30.0},
    "reliability.lint": {"good": 0.0, "bad": 20.0},
    "performance.lint": {"good": 0.0, "bad": 10.0},
    "safety.lint": {"good": 0.0, "bad": 20.0},
    # LLM 採点 (0-100, 高いほど良い) をそのまま使う恒等補間
    "interaction.doc": {"good": 100.0, "bad": 0.0},
    # QiU (forgejo collector)。クローズ/マージ所要日数の bad は個人運用の体感値、
    # クローズ率は「高いほど良い」の恒等補間。policy 側で要チューニング。
    "beneficialness.issue_close_days": {"good": 0.0, "bad": 30.0},
    "acceptability.issue_close_rate": {"good": 100.0, "bad": 0.0},
    "acceptability.pr_merge_days": {"good": 0.0, "bad": 14.0},
}

# kube-linter 由来の lint 集計でスコア化する特性 (実装は共通の _score_lint)
_LINT_CHARACTERISTICS = ("reliability", "performance", "safety")


def _score_security(
    metrics: list[Metric], policy: Policy, weight: float
) -> CharacteristicScore:
    """CVE 加重数 (Trivy) と SLSA check 通過率 (Macaron) のサブスコアを平均する.

    CVE は log 正規化、通過率は linear 正規化 (既定は恒等補間)。片方の collector
    がスキップされた場合は残った方だけでスコア化する (maintainability と同じ
    パーツ平均方式)。サンプル数はスキャン済みワークロード数 + 判定済み check 数。
    """
    by_id = {m.id: m for m in metrics}
    sev_weights = policy.severity_weights
    parts: list[tuple[float, list[str]]] = []  # (subscore, metric_ids)
    n_samples = 0

    # CVE サブスコア (trivy collector が動いた場合のみ。report_count が存在の証)
    report_count = by_id.get("security.scan.report_count")
    if report_count is not None:
        weighted_cve = 0.0
        cve_ids: list[str] = []
        for sev, w in sev_weights.items():
            m = by_id.get(f"security.cve.{sev}")
            if m is not None:
                weighted_cve += m.value * w
                cve_ids.append(m.id)
        th = policy.thresholds.get("security", _DEFAULT_THRESHOLDS["security"])
        parts.append(
            (normalize.logarithmic(weighted_cve, th["good"], th["bad"]), cve_ids)
        )
        n_samples += int(report_count.value)

    # SLSA サブスコア (macaron collector。判定済み check が無ければ出ない)
    rate = by_id.get("security.slsa.pass_rate")
    if rate is not None:
        th = policy.thresholds.get(
            "security.slsa", _DEFAULT_THRESHOLDS["security.slsa"]
        )
        parts.append((normalize.linear(rate.value, th["good"], th["bad"]), [rate.id]))
        evaluated = by_id.get("security.slsa.checks_evaluated")
        n_samples += int(evaluated.value) if evaluated is not None else 0

    insufficient = n_samples < policy.min_samples_for("security") or not parts
    score_val = sum(s for s, _ in parts) / len(parts) if parts else 0.0

    return CharacteristicScore(
        characteristic="security",
        score=round(score_val, 1),
        weight=weight,
        contributing_metrics=[mid for _, mids in parts for mid in mids],
        insufficient_data=insufficient,
    )


def _score_maintainability(
    metrics: list[Metric], policy: Policy, weight: float
) -> CharacteristicScore:
    """SonarQube 由来の比率系メジャー 2 つの linear 正規化を平均する.

    code_smells / cognitive_complexity は規模に比例して伸びるためスコアには使わず、
    収集メトリクス + Finding (analysis/rules.py) としてのみ扱う。
    """
    by_id = {m.id: m for m in metrics}
    parts: list[tuple[float, str]] = []  # (subscore, metric_id)

    debt = by_id.get("maintainability.debt_ratio")
    if debt is not None:
        th = policy.thresholds.get(
            "maintainability.debt_ratio", _DEFAULT_THRESHOLDS["maintainability.debt_ratio"]
        )
        parts.append((normalize.linear(debt.value, th["good"], th["bad"]), debt.id))

    dup = by_id.get("maintainability.duplicated_lines_density")
    if dup is not None:
        th = policy.thresholds.get(
            "maintainability.duplication", _DEFAULT_THRESHOLDS["maintainability.duplication"]
        )
        parts.append((normalize.linear(dup.value, th["good"], th["bad"]), dup.id))

    files = by_id.get("maintainability.files")
    n_samples = int(files.value) if files is not None else 0
    insufficient = n_samples < policy.min_samples_for("maintainability") or not parts

    score_val = sum(s for s, _ in parts) / len(parts) if parts else 0.0
    return CharacteristicScore(
        characteristic="maintainability",
        score=round(score_val, 1),
        weight=weight,
        contributing_metrics=[mid for _, mid in parts],
        insufficient_data=insufficient,
    )


def _score_lint(
    characteristic: str, metrics: list[Metric], policy: Policy, weight: float
) -> CharacteristicScore | None:
    """kube-linter 指摘件数の linear 正規化 (reliability/performance/safety 共通).

    集計メトリクス `<char>.lint.issues` が無ければ未収集として None を返す。
    サンプル数は lint.files (検査対象 YAML 数)。from_json 経路では省略される
    ことがあるため、欠落時は集計が存在する事実をもって十分とみなす。
    """
    by_id = {m.id: m for m in metrics}
    issues = by_id.get(f"{characteristic}.lint.issues")
    if issues is None:
        return None

    th = policy.thresholds.get(
        f"{characteristic}.lint", _DEFAULT_THRESHOLDS[f"{characteristic}.lint"]
    )
    score_val = normalize.linear(issues.value, th["good"], th["bad"])

    files = by_id.get("lint.files")
    insufficient = (
        files is not None and int(files.value) < policy.min_samples_for(characteristic)
    )

    return CharacteristicScore(
        characteristic=characteristic,
        score=round(score_val, 1),
        weight=weight,
        contributing_metrics=[issues.id],
        insufficient_data=insufficient,
    )


# ollama collector が出す副特性平均 (interaction スコアの入力)
_INTERACTION_METRIC_IDS = (
    "interaction.doc.self_descriptiveness",
    "interaction.doc.user_assistance",
    "interaction.doc.appropriateness",
)


def _score_interaction(
    metrics: list[Metric], policy: Policy, weight: float
) -> CharacteristicScore | None:
    """Ollama による文書評価 (副特性平均 3 つ) の linear 正規化を平均する.

    LLM 採点は元々 0-100 なので既定しきい値では恒等変換。判定を厳しくしたい
    場合は policy の interaction.doc.good を下げて再較正する。
    副特性平均が 1 つも無ければ未収集として None を返す。
    """
    by_id = {m.id: m for m in metrics}
    th = policy.thresholds.get("interaction.doc", _DEFAULT_THRESHOLDS["interaction.doc"])
    parts: list[tuple[float, str]] = []
    for mid in _INTERACTION_METRIC_IDS:
        m = by_id.get(mid)
        if m is not None:
            parts.append((normalize.linear(m.value, th["good"], th["bad"]), m.id))
    if not parts:
        return None

    evaluated = by_id.get("interaction.docs.evaluated")
    n_samples = int(evaluated.value) if evaluated is not None else 0
    insufficient = n_samples < policy.min_samples_for("interaction")

    score_val = sum(s for s, _ in parts) / len(parts)
    return CharacteristicScore(
        characteristic="interaction",
        score=round(score_val, 1),
        weight=weight,
        contributing_metrics=[mid for _, mid in parts],
        insufficient_data=insufficient,
    )


def _score_beneficialness(
    metrics: list[Metric], policy: Policy, weight: float
) -> CharacteristicScore | None:
    """forgejo collector の Issue クローズ所要日数 (中央値) を linear 正規化する.

    25019 有益性のうちルールベースで取れる効率性プロキシ。件数 (issues.total) が
    収集されていなければ collector スキップとして None を返す。クローズ済み
    Issue がまだ無い場合は insufficient data として総合から外す。
    """
    by_id = {m.id: m for m in metrics}
    total = by_id.get("forgejo.issues.total")
    if total is None:
        return None

    parts: list[tuple[float, str]] = []
    median = by_id.get("beneficialness.issue_close_days_median")
    if median is not None:
        th = policy.thresholds.get(
            "beneficialness.issue_close_days",
            _DEFAULT_THRESHOLDS["beneficialness.issue_close_days"],
        )
        parts.append((normalize.linear(median.value, th["good"], th["bad"]), median.id))

    insufficient = (
        int(total.value) < policy.min_samples_for("beneficialness") or not parts
    )
    score_val = sum(s for s, _ in parts) / len(parts) if parts else 0.0
    return CharacteristicScore(
        characteristic="beneficialness",
        score=round(score_val, 1),
        weight=weight,
        contributing_metrics=[mid for _, mid in parts],
        insufficient_data=insufficient,
    )


def _score_acceptability(
    metrics: list[Metric], policy: Policy, weight: float
) -> CharacteristicScore | None:
    """forgejo collector のクローズ率と PR マージ所要日数の linear 正規化を平均する.

    25019 受容性 (信頼) のルールベースプロキシ。件数 (issues.total / pulls.total)
    が収集されていなければ collector スキップとして None を返す。サンプル数は
    Issue と PR の合計で判定する。
    """
    by_id = {m.id: m for m in metrics}
    issues_total = by_id.get("forgejo.issues.total")
    pulls_total = by_id.get("forgejo.pulls.total")
    if issues_total is None and pulls_total is None:
        return None

    parts: list[tuple[float, str]] = []
    rate = by_id.get("acceptability.issue_close_rate")
    if rate is not None:
        th = policy.thresholds.get(
            "acceptability.issue_close_rate",
            _DEFAULT_THRESHOLDS["acceptability.issue_close_rate"],
        )
        parts.append((normalize.linear(rate.value, th["good"], th["bad"]), rate.id))

    merge_days = by_id.get("acceptability.pr_merge_days_median")
    if merge_days is not None:
        th = policy.thresholds.get(
            "acceptability.pr_merge_days",
            _DEFAULT_THRESHOLDS["acceptability.pr_merge_days"],
        )
        parts.append(
            (normalize.linear(merge_days.value, th["good"], th["bad"]), merge_days.id)
        )

    n_samples = sum(
        int(m.value) for m in (issues_total, pulls_total) if m is not None
    )
    insufficient = n_samples < policy.min_samples_for("acceptability") or not parts
    score_val = sum(s for s, _ in parts) / len(parts) if parts else 0.0
    return CharacteristicScore(
        characteristic="acceptability",
        score=round(score_val, 1),
        weight=weight,
        contributing_metrics=[mid for _, mid in parts],
        insufficient_data=insufficient,
    )


def score(analysis: Analysis, context: Context, policy: Policy) -> ScoreReport:
    """特性別スコアと CoU 重み付き総合スコアを算出する."""
    notes: list[str] = []
    characteristics: list[CharacteristicScore] = []

    if "security" in IMPLEMENTED:
        characteristics.append(
            _score_security(
                analysis.metrics, policy, context.weights.get("security", 0.0)
            )
        )

    if "maintainability" in IMPLEMENTED:
        maint_metrics = [
            m for m in analysis.metrics if m.characteristic == "maintainability"
        ]
        if maint_metrics:
            characteristics.append(
                _score_maintainability(
                    analysis.metrics,
                    policy,
                    context.weights.get("maintainability", 0.0),
                )
            )
        elif context.weights.get("maintainability", 0.0) > 0:
            # sonarqube collector がスキップされた場合 (token 未投入・未解析)。
            notes.append(
                "特性 'maintainability' はメトリクス未収集のため総合スコアから"
                "除外しました (sonarqube collector のスキップ?)"
            )

    for char in _LINT_CHARACTERISTICS:
        c = _score_lint(
            char, analysis.metrics, policy, context.weights.get(char, 0.0)
        )
        if c is not None:
            characteristics.append(c)
        elif context.weights.get(char, 0.0) > 0:
            notes.append(
                f"特性 '{char}' はメトリクス未収集のため総合スコアから"
                "除外しました (kubelinter collector のスキップ?)"
            )

    c = _score_interaction(
        analysis.metrics, policy, context.weights.get("interaction", 0.0)
    )
    if c is not None:
        characteristics.append(c)
    elif context.weights.get("interaction", 0.0) > 0:
        notes.append(
            "特性 'interaction' はメトリクス未収集のため総合スコアから"
            "除外しました (ollama collector のスキップ?)"
        )

    for char, scorer in (
        ("beneficialness", _score_beneficialness),
        ("acceptability", _score_acceptability),
    ):
        c = scorer(analysis.metrics, policy, context.weights.get(char, 0.0))
        if c is not None:
            characteristics.append(c)
        elif context.weights.get(char, 0.0) > 0:
            notes.append(
                f"特性 '{char}' はメトリクス未収集のため総合スコアから"
                "除外しました (forgejo collector のスキップ?)"
            )

    # CoU に重みがあるが未実装の特性は総合から除外し、note で明示する
    for char, w in context.weights.items():
        if char not in IMPLEMENTED and w > 0:
            notes.append(
                f"特性 '{char}' (CoU 重み {w}) は未実装のため総合スコアから除外しました"
            )

    # 重み付き総合: insufficient_data と weight<=0 を除外し再正規化
    usable = [
        c for c in characteristics if not c.insufficient_data and c.weight > 0
    ]
    for c in characteristics:
        if c.insufficient_data:
            notes.append(
                f"特性 '{c.characteristic}' はサンプル数不足のため総合から除外しました"
            )
    total_w = sum(c.weight for c in usable)
    if total_w > 0:
        overall = sum(c.score * c.weight for c in usable) / total_w
    else:
        overall = 0.0
        notes.append("総合スコア算出に使える特性がありませんでした")

    return ScoreReport(
        scored_at=now_iso(),
        target=analysis.target,
        context_id=context.context_id,
        characteristics=characteristics,
        overall=round(overall, 1),
        notes=notes,
    )
