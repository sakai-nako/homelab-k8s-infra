"""Forgejo Issue API の薄いクライアント (urllib のみ).

bridge が使う最小限のエンドポイントだけを実装する:
  - GET   /repos/{repo}/issues?state=open       (dedup 用の open Issue 走査)
  - POST  /repos/{repo}/issues                  (起票)
  - PATCH /repos/{repo}/issues/{n}              (body 更新 / close)
  - POST  /repos/{repo}/issues/{n}/comments     (状態変化コメント)
  - GET/POST /repos/{repo}/labels               (label の存在保証。失敗しても致命でない)

PAT スコープは write:issue を前提とする。label 系は権限不足で 403 になり得るため
best-effort 扱い (呼び出し側でフォールバック)。
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

# open Issue 走査の上限ページ数。1 ページ 50 件 × 4 = 200 件まで見れば
# 個人運用の同時 open アラート数としては十分。
_MAX_PAGES = 4
_PAGE_LIMIT = 50
_TIMEOUT_SEC = 15


class ForgejoError(Exception):
    """Forgejo API 呼び出しの失敗 (HTTP エラー / 接続不能)."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class ForgejoClient:
    def __init__(self, base_url: str, repo: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.repo = repo
        self._token = token

    # ---- 低レベル ----

    def _request(self, method: str, path: str, payload: dict | None = None) -> object:
        url = f"{self.base_url}/api/v1{path}"
        data = None
        headers = {
            "Authorization": f"token {self._token}",
            "Accept": "application/json",
        }
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT_SEC) as resp:
                body = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise ForgejoError(
                f"{method} {path} -> HTTP {exc.code}: {detail}", status=exc.code
            ) from exc
        except urllib.error.URLError as exc:
            raise ForgejoError(f"{method} {path} -> 接続失敗: {exc.reason}") from exc
        if not body:
            return None
        return json.loads(body)

    # ---- Issue ----

    def find_open_issue(self, marker: str) -> dict | None:
        """body に marker を含む open Issue を返す (無ければ None)."""
        for page in range(1, _MAX_PAGES + 1):
            query = urllib.parse.urlencode(
                {"state": "open", "type": "issues", "limit": _PAGE_LIMIT, "page": page}
            )
            issues = self._request("GET", f"/repos/{self.repo}/issues?{query}")
            if not issues:
                return None
            for issue in issues:
                if marker in (issue.get("body") or ""):
                    return issue
            if len(issues) < _PAGE_LIMIT:
                return None
        return None

    def create_issue(self, title: str, body: str, label_ids: list[int]) -> dict:
        payload: dict = {"title": title, "body": body}
        if label_ids:
            payload["labels"] = label_ids
        return self._request("POST", f"/repos/{self.repo}/issues", payload)

    def edit_issue(
        self, number: int, body: str | None = None, state: str | None = None
    ) -> None:
        payload: dict = {}
        if body is not None:
            payload["body"] = body
        if state is not None:
            payload["state"] = state
        self._request("PATCH", f"/repos/{self.repo}/issues/{number}", payload)

    def comment(self, number: int, body: str) -> None:
        self._request(
            "POST", f"/repos/{self.repo}/issues/{number}/comments", {"body": body}
        )

    # ---- Label (best-effort) ----

    def ensure_label_ids(self, names: list[str]) -> list[int]:
        """label 名 → id を解決し、無ければ作成する。

        write:issue PAT で label 作成が拒否される環境もあり得るため、
        失敗時は warning を出して空リストを返す (起票自体は継続させる)。
        """
        if not names:
            return []
        try:
            existing = self._request("GET", f"/repos/{self.repo}/labels?limit=50") or []
            by_name = {l["name"]: l["id"] for l in existing}
            ids = []
            for name in names:
                if name not in by_name:
                    created = self._request(
                        "POST",
                        f"/repos/{self.repo}/labels",
                        {"name": name, "color": "#d73a49"},
                    )
                    by_name[name] = created["id"]
                ids.append(by_name[name])
            return ids
        except ForgejoError as exc:
            logger.warning("label %s の解決に失敗 (label 無しで継続): %s", names, exc)
            return []
