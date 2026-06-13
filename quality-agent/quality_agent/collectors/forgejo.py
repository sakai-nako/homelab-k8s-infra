"""Forgejo API から Issue/PR メタデータを収集する collector.

ISO/IEC 25019:2023 の利用時の品質 (QiU) のうち、Git ホスティングのメタデータから
定量化できる 2 特性を扱う (docs/quality-model.md 有益性・受容性の行):
- beneficialness (有益性): Issue クローズまでの中央日数 (効率性)、
  長期滞留 Issue 数 (満足性の毀損プロキシ)
- acceptability (受容性): Issue クローズ率・PR マージまでの中央日数 (信頼)

センチメント分類など意味理解が必要な評価は Ollama 経路の将来拡張とし、
ここではルールベースで算出できるメトリクスのみ集計する。

データ取得は他 collector と同じ 2 経路:
- デフォルト: Forgejo REST API (/api/v1) を urllib で直接叩く
- `from_json`: 事前保存したレスポンス JSON から読む (オフライン検証・試験性)。
  形式: {"issues": [<Issue>...], "pulls": [<PullRequest>...]}

トークン未提供・接続不可・スコープ不足は ForgejoUnavailable を上げ、呼び出し側
(cli) が警告してこの source だけスキップする (sonarqube と同パターン)。
PAT には read:repository に加えて read:issue スコープが必要
(/repos/{owner}/{repo}/issues が read:issue を要求するため)。
"""

from __future__ import annotations

import json
import statistics
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from ..models import Metric

DEFAULT_URL = "http://forgejo-http.forgejo.svc.cluster.local:3000"
DEFAULT_REPO = "sakai/local-infra"

# Forgejo API のページサイズ上限 (DEFAULT_PAGING_NUM)。これ未満の応答で打ち切る
_PAGE_LIMIT = 50
# ページング暴走防止 (個人リポジトリでは到達しない想定。到達時は warning)
_MAX_PAGES = 10

# 「長期滞留」とみなす未解決 Issue の経過日数
STALE_DAYS = 90.0

_SECONDS_PER_DAY = 86400.0


class ForgejoUnavailable(RuntimeError):
    """Forgejo 収集をスキップすべき状況 (token 未設定・スコープ不足・接続不可)."""


def _api_get(base_url: str, path: str, token: str, params: dict[str, Any]) -> Any:
    query = urllib.parse.urlencode(params)
    url = f"{base_url.rstrip('/')}/api/v1/{path}?{query}"
    req = urllib.request.Request(url, headers={"Authorization": f"token {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise ForgejoUnavailable(
                f"認証またはスコープ不足です (HTTP {e.code})。"
                "PAT には read:repository,read:issue が必要です"
                " (SealedSecret forgejo-read を確認)"
            ) from e
        if e.code == 404:
            raise ForgejoUnavailable(f"リポジトリが見つかりません: {path}") from e
        raise ForgejoUnavailable(f"API エラー (HTTP {e.code}): {path}") from e
    except urllib.error.URLError as e:
        raise ForgejoUnavailable(f"Forgejo に接続できません: {e.reason}") from e


def _paged(base_url: str, path: str, token: str, params: dict[str, Any]) -> list[dict]:
    """limit/page でページングしながら全件取得する."""
    items: list[dict] = []
    for page in range(1, _MAX_PAGES + 1):
        batch = _api_get(
            base_url, path, token, {**params, "limit": _PAGE_LIMIT, "page": page}
        )
        if not isinstance(batch, list):
            raise ForgejoUnavailable(f"想定外のレスポンス形式です: {path}")
        items.extend(batch)
        if len(batch) < _PAGE_LIMIT:
            return items
    print(
        f"warning: {path} が {_MAX_PAGES} ページを超えたため打ち切りました"
        f" ({len(items)} 件まで収集)",
        file=sys.stderr,
    )
    return items


def _parse_ts(value: Any) -> datetime | None:
    """RFC3339 文字列を aware datetime にする。未設定 (null / 0001 年) は None."""
    if not value or not isinstance(value, str):
        return None
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.year <= 1:  # Forgejo は未設定日時を 0001-01-01 で返すことがある
        return None
    return ts


def _days_between(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return (end - start).total_seconds() / _SECONDS_PER_DAY


def collect(
    *,
    repo: str = DEFAULT_REPO,
    base_url: str = DEFAULT_URL,
    token: str | None = None,
    from_json: str | None = None,
    now: datetime | None = None,
) -> list[Metric]:
    """Issue/PR メタデータを集計して Metric のリストを返す."""
    if from_json:
        with open(from_json, "r", encoding="utf-8") as f:
            data = json.load(f)
        issues = data.get("issues", [])
        pulls = data.get("pulls", [])
    else:
        if not token:
            raise ForgejoUnavailable(
                "FORGEJO_TOKEN が未設定です (SealedSecret forgejo-read 未投入?)"
            )
        # type=issues で PR を除外する (Forgejo の Issue API は PR も混ぜて返すため)
        issues = _paged(
            base_url, f"repos/{repo}/issues", token, {"state": "all", "type": "issues"}
        )
        pulls = _paged(base_url, f"repos/{repo}/pulls", token, {"state": "all"})

    if now is None:
        now = datetime.now(timezone.utc)
    labels = {"repo": repo}

    n_open = sum(1 for i in issues if i.get("state") == "open")
    n_closed = sum(1 for i in issues if i.get("state") == "closed")
    merged_pulls = [p for p in pulls if p.get("merged")]

    close_days = [
        d
        for i in issues
        if i.get("state") == "closed"
        if (d := _days_between(_parse_ts(i.get("created_at")), _parse_ts(i.get("closed_at"))))
        is not None
    ]
    merge_days = [
        d
        for p in merged_pulls
        if (d := _days_between(_parse_ts(p.get("created_at")), _parse_ts(p.get("merged_at"))))
        is not None
    ]
    n_stale = sum(
        1
        for i in issues
        if i.get("state") == "open"
        if (age := _days_between(_parse_ts(i.get("created_at")), now)) is not None
        and age > STALE_DAYS
    )

    # 件数 (スコアラの insufficient data 判定にも使う)
    counts: list[tuple[str, str, float]] = [
        ("forgejo.issues.total", "Issue 総数", float(len(issues))),
        ("forgejo.issues.open", "未解決 Issue 数", float(n_open)),
        ("forgejo.issues.closed", "解決済み Issue 数", float(n_closed)),
        ("forgejo.pulls.total", "PR 総数", float(len(pulls))),
        ("forgejo.pulls.merged", "マージ済み PR 数", float(len(merged_pulls))),
    ]
    metrics: list[Metric] = [
        Metric(
            id=mid,
            characteristic="acceptability",
            name=name,
            value=value,
            unit="count",
            source="forgejo",
            labels=dict(labels),
        )
        for mid, name, value in counts
    ]

    metrics.append(
        Metric(
            id="beneficialness.issue_stale_open",
            characteristic="beneficialness",
            subcharacteristic="satisfaction",
            name=f"{int(STALE_DAYS)} 日以上未解決の Issue 数",
            value=float(n_stale),
            unit="count",
            source="forgejo",
            labels=dict(labels),
        )
    )

    if issues:
        metrics.append(
            Metric(
                id="acceptability.issue_close_rate",
                characteristic="acceptability",
                subcharacteristic="trust",
                name="Issue クローズ率",
                value=round(100.0 * n_closed / len(issues), 1),
                unit="percent",
                source="forgejo",
                labels=dict(labels),
            )
        )
    if close_days:
        metrics.append(
            Metric(
                id="beneficialness.issue_close_days_median",
                characteristic="beneficialness",
                subcharacteristic="efficiency",
                name="Issue クローズ所要日数 (中央値)",
                value=round(statistics.median(close_days), 1),
                unit="days",
                source="forgejo",
                labels=dict(labels),
            )
        )
    if merge_days:
        metrics.append(
            Metric(
                id="acceptability.pr_merge_days_median",
                characteristic="acceptability",
                subcharacteristic="trust",
                name="PR マージ所要日数 (中央値)",
                value=round(statistics.median(merge_days), 1),
                unit="days",
                source="forgejo",
                labels=dict(labels),
            )
        )
    return metrics
