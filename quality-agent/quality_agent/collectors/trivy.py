"""Trivy (trivy-operator) の VulnerabilityReport を集計する collector.

クラスタ内で trivy-operator が全 namespace に生成する
`vulnerabilityreports.aquasecurity.github.io` を読み、severity 別 CVE 件数を
セキュリティ特性のメトリクスに正規化する。

データ取得は 2 経路:
- デフォルト: `kubectl get ... -o json` を subprocess 実行 (in-cluster / WSL 双方で動く)
- `from_json`: 事前に保存した JSON ファイルから読む (オフライン検証・試験性のため)
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

from ..models import Metric

CRD = "vulnerabilityreports.aquasecurity.github.io"

# summary の *Count キー -> 正規化後の severity ラベル
_SEVERITY_KEYS = {
    "criticalCount": "critical",
    "highCount": "high",
    "mediumCount": "medium",
    "lowCount": "low",
    "unknownCount": "unknown",
}

_SEVERITY_NAMES_JA = {
    "critical": "緊急",
    "high": "高",
    "medium": "中",
    "low": "低",
    "unknown": "不明",
}


def _load_reports(
    from_json: str | None, namespace: str | None, kubectl_bin: str
) -> dict[str, Any]:
    if from_json:
        with open(from_json, "r", encoding="utf-8") as f:
            return json.load(f)
    cmd = [kubectl_bin, "get", CRD, "-o", "json"]
    if namespace:
        cmd += ["-n", namespace]
    else:
        cmd += ["-A"]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(proc.stdout)


def collect(
    *,
    from_json: str | None = None,
    namespace: str | None = None,
    kubectl_bin: str = "kubectl",
) -> list[Metric]:
    """VulnerabilityReport を集計して Metric のリストを返す."""
    data = _load_reports(from_json, namespace, kubectl_bin)
    items = data.get("items", [])

    totals = {sev: 0 for sev in _SEVERITY_KEYS.values()}
    for item in items:
        summary = item.get("report", {}).get("summary", {})
        for raw_key, sev in _SEVERITY_KEYS.items():
            totals[sev] += int(summary.get(raw_key, 0) or 0)

    metrics: list[Metric] = []
    for sev, count in totals.items():
        metrics.append(
            Metric(
                id=f"security.cve.{sev}",
                characteristic="security",
                subcharacteristic="resistance",
                name=f"CVE 件数 ({_SEVERITY_NAMES_JA[sev]})",
                value=float(count),
                unit="count",
                source="trivy",
            )
        )

    # スキャン済みワークロード数 = サンプル数 (insufficient data 判定に使う)
    metrics.append(
        Metric(
            id="security.scan.report_count",
            characteristic="security",
            subcharacteristic="resistance",
            name="スキャン済みワークロード数",
            value=float(len(items)),
            unit="count",
            source="trivy",
        )
    )
    return metrics
