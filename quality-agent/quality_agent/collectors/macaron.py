"""Macaron の SLSA 監査レポートを集計する collector.

nightly Job の macaron initContainer がローカル clone を解析して出力した
レポート JSON (`reports/local_repos/<name>/<name>.json`) を読み、check 結果を
security 特性の耐性 (resistance) サブ特性メトリクスに正規化する
(docs/quality-model.md のセキュリティ行「SLSA レベル判定」に対応)。

データ取得は単一経路 (レポートファイルを読むのみ)。`report_path` には
ファイルパスのほかディレクトリも渡せる。レポートのファイル名は対象リポジトリの
origin URL から導出され固定できない (例: `local-infra.git` -> `local-infra_git.json`)
ため、Job からはレポートルート (`/src/reports`) を渡して再帰探索させる。

initContainer が失敗・スキップした場合はレポートが存在しないので
MacaronUnavailable を上げ、呼び出し側がこの source だけスキップする
(kubelinter / sonarqube collector と同パターン)。

スコアの母集団について: ローカル Forgejo リポジトリの解析では GitHub API や
deps.dev に依存する check が UNKNOWN / SKIPPED になる。これらは「判定不能」で
あって失敗ではないため、pass_rate の分母は PASSED + FAILED のみとする。
"""

from __future__ import annotations

import json
import os
from typing import Any

from ..models import Metric

# レポートと同じディレクトリに出る依存解析レポート (集計対象外)
_EXCLUDED_FILENAMES = {"dependencies.json"}


class MacaronUnavailable(RuntimeError):
    """Macaron 収集をスキップすべき状況 (レポート無し・パース不能)."""


def _find_report(path: str) -> str:
    """レポート JSON のパスを解決する. ディレクトリなら再帰探索する."""
    if os.path.isfile(path):
        return path
    if not os.path.isdir(path):
        raise MacaronUnavailable(
            f"レポートがありません: {path} (macaron initContainer の失敗/スキップ?)"
        )
    candidates = []
    for root, _dirs, files in os.walk(path):
        for f in sorted(files):
            if f.endswith(".json") and f not in _EXCLUDED_FILENAMES:
                candidates.append(os.path.join(root, f))
    if not candidates:
        raise MacaronUnavailable(
            f"レポート JSON が見つかりません: {path} (macaron 解析の失敗?)"
        )
    if len(candidates) > 1:
        # 解析対象は単一リポジトリの想定。複数あると集計対象を誤るので明示的に拒否
        raise MacaronUnavailable(
            f"レポート JSON が複数あります: {', '.join(candidates)}"
        )
    return candidates[0]


def _load(report_path: str | None) -> dict[str, Any]:
    if not report_path:
        raise MacaronUnavailable("レポートパス未指定 (--macaron-report)")
    path = _find_report(report_path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise MacaronUnavailable(f"レポートをパースできません: {path}: {e}") from e


def collect(*, report_path: str | None) -> list[Metric]:
    """Macaron レポートを集計して Metric のリストを返す."""
    data = _load(report_path)

    checks = data.get("target", {}).get("checks", {})
    results = checks.get("results")
    summary = checks.get("summary")
    if results is None or summary is None:
        # 想定スキーマでない出力を「check ゼロ」と誤読しないための防御
        raise MacaronUnavailable("レポートに target.checks.results/summary がありません")

    # 解析対象コミット (時系列上でどの時点の監査かを識別する)
    commit = str(data.get("target", {}).get("info", {}).get("commit_hash", ""))[:12]
    common_labels = {"commit": commit} if commit else {}

    metrics: list[Metric] = []
    for r in sorted(results, key=lambda r: str(r.get("check_id", ""))):
        check_id = r.get("check_id", "")
        result_type = r.get("result_type", "UNKNOWN")
        if not check_id:
            continue
        metrics.append(
            Metric(
                id=f"slsa.check.{check_id}",
                characteristic="security",
                subcharacteristic="resistance",
                name=f"Macaron: {check_id}",
                value=1.0 if result_type == "PASSED" else 0.0,
                unit="bool",
                source="macaron",
                labels={**common_labels, "result": result_type},
            )
        )

    passed = int(summary.get("PASSED", 0) or 0)
    failed = int(summary.get("FAILED", 0) or 0)
    evaluated = passed + failed

    for mid, name, value in (
        ("security.slsa.checks_passed", "Macaron check 通過数", float(passed)),
        ("security.slsa.checks_failed", "Macaron check 失敗数", float(failed)),
        # 判定が出た check 数 = サンプル数 (insufficient data 判定に使う)
        ("security.slsa.checks_evaluated", "Macaron 判定済み check 数", float(evaluated)),
    ):
        metrics.append(
            Metric(
                id=mid,
                characteristic="security",
                subcharacteristic="resistance",
                name=name,
                value=value,
                unit="count",
                source="macaron",
                labels=dict(common_labels),
            )
        )

    if evaluated > 0:
        metrics.append(
            Metric(
                id="security.slsa.pass_rate",
                characteristic="security",
                subcharacteristic="resistance",
                name="Macaron check 通過率",
                value=round(100.0 * passed / evaluated, 1),
                unit="percent",
                source="macaron",
                labels=dict(common_labels),
            )
        )
    return metrics
