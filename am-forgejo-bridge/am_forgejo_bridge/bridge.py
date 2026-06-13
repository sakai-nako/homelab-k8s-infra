"""Alertmanager webhook payload → Forgejo Issue 操作の中核ロジック.

Alertmanager は通知をアラートグループ単位 (route の group_by で決まる) で送る。
本 bridge は「1 グループ = 1 open Issue」を不変条件とし、groupKey の digest を
HTML コメントとして Issue body に埋め込んで dedup する:

  firing (open Issue 無し)        → 起票
  firing (firing 集合に変化無し)   → 何もしない (repeat_interval 通知の吸収)
  firing (firing 集合が変化)       → body を最新状態に PATCH + 差分コメント
  resolved                        → resolve コメント + close

resolve 後の再発火は (closed Issue は走査しないので) 新しい Issue になる。
Forgejo API の失敗は ForgejoError のまま上へ投げ、HTTP 500 で応答して
Alertmanager 側のリトライに任せる。
"""

from __future__ import annotations

import hashlib
import logging

from .forgejo import ForgejoClient

logger = logging.getLogger(__name__)

_MARKER_PREFIX = "<!-- am-forgejo-bridge group:"
_FPS_PREFIX = "<!-- am-forgejo-bridge fps:"


def group_digest(group_key: str) -> str:
    return hashlib.sha256(group_key.encode("utf-8")).hexdigest()[:16]


def group_marker(digest: str) -> str:
    return f"{_MARKER_PREFIX}{digest} -->"


def fps_marker(fps: list[str]) -> str:
    return f"{_FPS_PREFIX}{','.join(fps)} -->"


def parse_fps(body: str) -> list[str]:
    for line in body.splitlines():
        line = line.strip()
        if line.startswith(_FPS_PREFIX) and line.endswith("-->"):
            csv = line[len(_FPS_PREFIX) : -len("-->")].strip()
            return sorted(p for p in csv.split(",") if p)
    return []


def build_title(group_labels: dict) -> str:
    if not group_labels:
        return "[alert] (グループラベル無し)"
    pairs = ", ".join(f"{k}={group_labels[k]}" for k in sorted(group_labels))
    return f"[alert] {pairs}"


def _alert_section(alert: dict) -> str:
    labels = alert.get("labels", {})
    annotations = alert.get("annotations", {})
    lines = [f"### {labels.get('alertname', '(alertname 無し)')} — `{alert.get('status', '?')}`"]
    summary = annotations.get("summary") or annotations.get("description")
    if summary:
        lines.append(f"> {summary}")
    lines.append("")
    lines.append("| label | value |")
    lines.append("| :--- | :--- |")
    for key in sorted(labels):
        lines.append(f"| {key} | `{labels[key]}` |")
    if alert.get("startsAt"):
        lines.append(f"\n- 開始: `{alert['startsAt']}`")
    runbook = annotations.get("runbook_url")
    if runbook:
        lines.append(f"- runbook: {runbook}")
    return "\n".join(lines)


def build_body(payload: dict, digest: str, fps: list[str]) -> str:
    firing = [a for a in payload.get("alerts", []) if a.get("status") == "firing"]
    resolved = [a for a in payload.get("alerts", []) if a.get("status") == "resolved"]
    parts = [
        group_marker(digest),
        fps_marker(fps),
        "",
        "Alertmanager からの自動起票 (am-forgejo-bridge)。"
        "条件が解消すると自動でクローズされます。",
        "",
        f"- firing: **{len(firing)}** / resolved: {len(resolved)}",
        f"- 受信 groupKey: `{payload.get('groupKey', '?')}`",
        f"- [Alertmanager UI](http://alertmanager.local.test) / "
        f"[Grafana Alerting](http://grafana.local.test/alerting/list)",
        "",
    ]
    for alert in firing:
        parts.append(_alert_section(alert))
        parts.append("")
    return "\n".join(parts)


class Bridge:
    def __init__(self, client: ForgejoClient, labels: list[str] | None = None):
        self._client = client
        self._labels = labels if labels is not None else ["alert"]

    def handle(self, payload: dict) -> str:
        """通知 1 件を処理し、実行したアクション名を返す (ログ/テスト用)."""
        group_key = payload.get("groupKey", "")
        digest = group_digest(group_key)
        marker = group_marker(digest)
        firing = [a for a in payload.get("alerts", []) if a.get("status") == "firing"]
        issue = self._client.find_open_issue(marker)

        # payload 全体の status は firing だが firing 配列が空、というケースは
        # 実質 resolve とみなす (send_resolved の境界タイミング)。
        if payload.get("status") == "firing" and firing:
            fps = sorted(a.get("fingerprint", "") for a in firing)
            if issue is None:
                title = build_title(payload.get("groupLabels", {}))
                body = build_body(payload, digest, fps)
                label_ids = self._client.ensure_label_ids(self._labels)
                created = self._client.create_issue(title, body, label_ids)
                logger.info("起票 #%s: %s", created.get("number"), title)
                return "created"
            old_fps = parse_fps(issue.get("body") or "")
            if old_fps == fps:
                logger.info("変化なし (issue #%s)", issue.get("number"))
                return "unchanged"
            body = build_body(payload, digest, fps)
            self._client.edit_issue(issue["number"], body=body)
            added = sorted(set(fps) - set(old_fps))
            removed = sorted(set(old_fps) - set(fps))
            self._client.comment(
                issue["number"],
                "firing 中のアラート集合が変化しました "
                f"(+{len(added)} / -{len(removed)})。本文を最新状態に更新済み。",
            )
            logger.info("更新 (issue #%s)", issue.get("number"))
            return "updated"

        # resolved
        if issue is None:
            logger.info("resolve 通知に対応する open Issue 無し (group %s)", digest)
            return "ignored"
        self._client.comment(
            issue["number"], "全アラートが resolve されました。自動クローズします。"
        )
        self._client.edit_issue(issue["number"], state="closed")
        logger.info("クローズ (issue #%s)", issue.get("number"))
        return "closed"
