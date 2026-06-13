"""ScoreReport を人間可読テキスト / JSON にレンダリングする."""

from __future__ import annotations

import json

from ..models import Analysis, ScoreReport


def render_json(report: ScoreReport) -> str:
    return json.dumps(report.to_dict(), ensure_ascii=False, indent=2)


def render_text(report: ScoreReport, analysis: Analysis | None = None) -> str:
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("  品質評価レポート (quality-agent)")
    lines.append("=" * 60)
    lines.append(f"  対象        : {report.target}")
    lines.append(f"  コンテキスト: {report.context_id}")
    lines.append(f"  評価時刻    : {report.scored_at}")
    lines.append("-" * 60)
    lines.append(f"  総合スコア  : {report.overall:5.1f} / 100")
    lines.append("-" * 60)
    lines.append("  特性別スコア:")
    for c in report.characteristics:
        flag = "  [データ不足]" if c.insufficient_data else ""
        lines.append(
            f"    - {c.characteristic:<14} {c.score:5.1f} / 100"
            f"  (CoU重み {c.weight:.2f}){flag}"
        )

    if analysis is not None and analysis.findings:
        lines.append("-" * 60)
        lines.append("  所見:")
        for f in analysis.findings:
            mark = {"critical": "✗", "warn": "!", "info": "·"}.get(f.severity, "·")
            lines.append(f"    {mark} [{f.severity}] {f.message}")

    if report.notes:
        lines.append("-" * 60)
        lines.append("  注記:")
        for n in report.notes:
            lines.append(f"    - {n}")

    lines.append("=" * 60)
    return "\n".join(lines)
