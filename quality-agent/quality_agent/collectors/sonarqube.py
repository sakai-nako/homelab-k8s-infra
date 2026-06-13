"""SonarQube Web API から保守性メトリクスを取得する collector.

`api/measures/component` を叩き、SonarQube が算出した保守性系メジャーを
maintainability 特性のメトリクスに正規化する (docs/quality-model.md 保守性行)。
依存最小方針に従い HTTP は標準ライブラリ urllib のみで行う。

データ取得は trivy collector と同じ 2 経路:
- デフォルト: Web API を直接叩く (in-cluster / WSL port-forward 双方で動く)
- `from_json`: 事前に保存した API レスポンス JSON から読む (オフライン検証・試験性)

認証はユーザートークン (squ_...) の Bearer ヘッダ。トークン未提供・接続不可・
プロジェクト未解析の場合は SonarQubeUnavailable を上げ、呼び出し側 (cli) が
警告してこの source だけをスキップする。SealedSecret 投入前でも nightly の
他 collector を止めないための GitOps eventual 収束パターン。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from ..models import Metric

DEFAULT_URL = "http://sonarqube-sonarqube.sonarqube.svc.cluster.local:9000"

# api/measures/component に要求するメトリクスキー
METRIC_KEYS = [
    "ncloc",
    "files",
    "sqale_index",
    "sqale_debt_ratio",
    "code_smells",
    "cognitive_complexity",
    "duplicated_lines_density",
]

# SonarQube メジャー -> (Metric id, 副特性, 人間可読名, 単位)
_MEASURE_MAP: dict[str, tuple[str, str, str, str]] = {
    "sqale_debt_ratio": (
        "maintainability.debt_ratio",
        "modifiability",
        "技術的負債比",
        "percent",
    ),
    "duplicated_lines_density": (
        "maintainability.duplicated_lines_density",
        "reusability",
        "重複行密度",
        "percent",
    ),
    "code_smells": (
        "maintainability.code_smells",
        "analysability",
        "コードスメル件数",
        "count",
    ),
    "cognitive_complexity": (
        "maintainability.cognitive_complexity",
        "analysability",
        "認知的複雑度 (合計)",
        "count",
    ),
    "sqale_index": (
        "maintainability.tech_debt_minutes",
        "modifiability",
        "技術的負債 (分換算)",
        "minutes",
    ),
    "ncloc": (
        "maintainability.ncloc",
        "analysability",
        "有効コード行数",
        "lines",
    ),
    "files": (
        "maintainability.files",
        "analysability",
        "解析済みファイル数",
        "count",
    ),
}


class SonarQubeUnavailable(RuntimeError):
    """SonarQube からの収集をスキップすべき状況 (token 未投入・未解析・接続不可)."""


def _fetch_measures(base_url: str, component: str, token: str) -> dict[str, Any]:
    query = urllib.parse.urlencode(
        {"component": component, "metricKeys": ",".join(METRIC_KEYS)}
    )
    url = f"{base_url.rstrip('/')}/api/measures/component?{query}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise SonarQubeUnavailable(
                f"認証に失敗しました (HTTP {e.code})。SONARQUBE_TOKEN を確認してください"
            ) from e
        if e.code == 404:
            raise SonarQubeUnavailable(
                f"プロジェクト '{component}' が見つかりません。"
                "sonar-scanner がまだ一度も解析していない可能性があります"
            ) from e
        raise SonarQubeUnavailable(f"API エラー (HTTP {e.code})") from e
    except urllib.error.URLError as e:
        raise SonarQubeUnavailable(f"SonarQube に接続できません: {e.reason}") from e


def collect(
    *,
    component: str,
    base_url: str = DEFAULT_URL,
    token: str | None = None,
    from_json: str | None = None,
) -> list[Metric]:
    """保守性メジャーを集計して Metric のリストを返す."""
    if from_json:
        with open(from_json, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        if not token:
            raise SonarQubeUnavailable(
                "SONARQUBE_TOKEN が未設定です (SealedSecret 未投入?)"
            )
        data = _fetch_measures(base_url, component, token)

    measures = data.get("component", {}).get("measures", [])
    metrics: list[Metric] = []
    for m in measures:
        mapped = _MEASURE_MAP.get(m.get("metric", ""))
        if mapped is None:
            continue  # 要求外のメジャーは無視 (将来 API が増やしても壊れない)
        metric_id, subchar, name, unit = mapped
        try:
            value = float(m["value"])
        except (KeyError, TypeError, ValueError):
            continue  # value を持たないメジャー (新規プロジェクト等) はスキップ
        metrics.append(
            Metric(
                id=metric_id,
                characteristic="maintainability",
                subcharacteristic=subchar,
                name=name,
                value=value,
                unit=unit,
                source="sonarqube",
            )
        )
    return metrics
