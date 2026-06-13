"""ルールベース解析. メトリクスから Finding を導く."""

from __future__ import annotations

from ..models import Analysis, Collection, Finding


def analyze(collection: Collection) -> Analysis:
    """Collection を解析し Finding を付与した Analysis を返す."""
    by_id = {m.id: m for m in collection.metrics}
    findings: list[Finding] = []

    crit = by_id.get("security.cve.critical")
    if crit is not None and crit.value > 0:
        findings.append(
            Finding(
                id="sec.critical-cve",
                characteristic="security",
                severity="critical",
                message=f"緊急 (Critical) CVE が {int(crit.value)} 件検出されています",
                metric_id=crit.id,
            )
        )

    high = by_id.get("security.cve.high")
    if high is not None and high.value > 0:
        findings.append(
            Finding(
                id="sec.high-cve",
                characteristic="security",
                severity="warn",
                message=f"高 (High) CVE が {int(high.value)} 件検出されています",
                metric_id=high.id,
            )
        )

    # 保守性: SonarQube レーティング D 境界 (20%) を超えたら警告する。
    debt = by_id.get("maintainability.debt_ratio")
    if debt is not None and debt.value > 20.0:
        findings.append(
            Finding(
                id="maint.high-debt-ratio",
                characteristic="maintainability",
                severity="warn",
                message=(
                    f"技術的負債比が {debt.value:.1f}% です"
                    " (SonarQube レーティング D 相当の 20% 超)"
                ),
                metric_id=debt.id,
            )
        )

    dup = by_id.get("maintainability.duplicated_lines_density")
    if dup is not None and dup.value > 20.0:
        findings.append(
            Finding(
                id="maint.high-duplication",
                characteristic="maintainability",
                severity="warn",
                message=f"重複行密度が {dup.value:.1f}% です (20% 超)",
                metric_id=dup.id,
            )
        )

    # kube-linter: 特権コンテナ・ホスト共有系は件数ゼロを保ちたいので warn を出す。
    for check in ("privileged-container", "host-network", "host-pid", "docker-sock"):
        m = by_id.get(f"lint.check.{check}")
        if m is not None and m.value > 0:
            findings.append(
                Finding(
                    id=f"safety.lint-{check}",
                    characteristic="safety",
                    severity="warn",
                    message=f"kube-linter: {check} の指摘が {int(m.value)} 件あります",
                    metric_id=m.id,
                )
            )

    perf_lint = by_id.get("performance.lint.issues")
    if perf_lint is not None and perf_lint.value > 0:
        findings.append(
            Finding(
                id="perf.unset-resources",
                characteristic="performance",
                severity="info",
                message=(
                    f"resource requests/limits 未設定の指摘が {int(perf_lint.value)} 件"
                    "あります (kube-linter)"
                ),
                metric_id=perf_lint.id,
            )
        )

    # 相互作用性: 評価最低のドキュメントが「改善余地あり」域 (<50) なら指す。
    # どの文書から直すべきかを示す actionable な所見にする (doc ラベル参照)。
    worst = by_id.get("interaction.doc.min_score")
    if worst is not None and worst.value < 50.0:
        findings.append(
            Finding(
                id="interaction.low-doc-score",
                characteristic="interaction",
                severity="warn",
                message=(
                    f"ドキュメント '{worst.labels.get('doc', '?')}' の LLM 評価が"
                    f" {worst.value:.0f} 点です (50 点未満: 自己記述性・手順の"
                    "実行可能性・冒頭の目的説明を見直す)"
                ),
                metric_id=worst.id,
            )
        )

    # 有益性: 長期滞留 Issue は未充足のユーザーニーズの放置を示す。
    stale = by_id.get("beneficialness.issue_stale_open")
    if stale is not None and stale.value > 0:
        findings.append(
            Finding(
                id="beneficialness.stale-issues",
                characteristic="beneficialness",
                severity="warn",
                message=(
                    f"90 日以上未解決の Issue が {int(stale.value)} 件あります"
                    " (クローズするか対応方針を明記する)"
                ),
                metric_id=stale.id,
            )
        )

    # 受容性: クローズ率の低迷。少サンプルの過剰反応を避けるため母数 5 件以上で判定。
    total = by_id.get("forgejo.issues.total")
    rate = by_id.get("acceptability.issue_close_rate")
    if (
        total is not None
        and total.value >= 5
        and rate is not None
        and rate.value < 50.0
    ):
        findings.append(
            Finding(
                id="acceptability.low-close-rate",
                characteristic="acceptability",
                severity="warn",
                message=f"Issue クローズ率が {rate.value:.0f}% です (50% 未満)",
                metric_id=rate.id,
            )
        )

    # セキュリティ (耐性): 悪性メタデータ検出 (Macaron が OSV の malicious DB と
    # 照合する check) の FAILED は「悪性の兆候あり」を意味するので最優先で指す。
    malicious = by_id.get("slsa.check.mcn_detect_malicious_metadata_1")
    if (
        malicious is not None
        and malicious.labels.get("result") == "FAILED"
    ):
        findings.append(
            Finding(
                id="sec.malicious-metadata",
                characteristic="security",
                severity="critical",
                message=(
                    "Macaron が悪性メタデータの兆候を検出しました"
                    " (mcn_detect_malicious_metadata_1 FAILED)"
                ),
                metric_id=malicious.id,
            )
        )

    # セキュリティ (耐性): ビルド来歴 (SLSA provenance) の未整備。個人運用の
    # 現状では常時 FAILED が想定値なので info に留める (改善は CI 導入後)。
    provenance = by_id.get("slsa.check.mcn_provenance_available_1")
    if (
        provenance is not None
        and provenance.labels.get("result") == "FAILED"
    ):
        findings.append(
            Finding(
                id="sec.no-slsa-provenance",
                characteristic="security",
                severity="info",
                message=(
                    "SLSA provenance が公開されていません"
                    " (ビルド来歴の第三者検証が不可能)"
                ),
                metric_id=provenance.id,
            )
        )

    # NOTE(Phase 4): 意味理解系所見 (要件↔テスト整合, Issue sentiment など) は
    # Ollama 経路の将来拡張としてここに追加する。

    return Analysis(
        collected_at=collection.collected_at,
        target=collection.target,
        metrics=collection.metrics,
        findings=findings,
    )
