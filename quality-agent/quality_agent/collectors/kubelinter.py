"""kube-linter で k8s マニフェストを静的解析する collector.

リポジトリの manifests/ ディレクトリを `kube-linter lint --format json` で検査し、
check 名を ISO 25010 の特性 (reliability / performance / safety) にマッピングして
件数を集計する (docs/quality-model.md の信頼性・性能効率性・安全性の行に対応)。

データ取得は他 collector と同じ 2 経路:
- デフォルト: kube-linter バイナリを subprocess 実行 (イメージに同梱)
- `from_json`: 事前に保存した lint 出力 JSON から読む (オフライン検証・試験性)

kube-linter は指摘が 1 件でもあると exit code 1 を返す仕様のため、0/1 は正常、
それ以外 (パース不能・バイナリ無し・対象パス無し) は KubeLinterUnavailable を
上げて呼び出し側がこの source だけスキップする (sonarqube collector と同パターン。
nightly では clone 用 PAT 未投入時に対象パスが無いケースがこれに当たる)。
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from ..models import Metric

# 明示的に有効化する非デフォルト check (信頼性スコアの主要素)
EXTRA_CHECKS = ["no-liveness-probe", "no-readiness-probe"]

# check 名 -> 特性のマッピング。ここに無い check は reliability に倒す
# (kube-linter のデフォルト check は信頼性系が最多のため)。
_PERFORMANCE_CHECKS = {
    "unset-cpu-requirements",
    "unset-memory-requirements",
}
_SAFETY_CHECKS = {
    "run-as-non-root",
    "no-read-only-root-fs",
    "privileged-container",
    "privilege-escalation-container",
    "host-network",
    "host-ipc",
    "host-pid",
    "docker-sock",
    "drop-net-raw-capability",
    "env-var-secret",
    "read-secret-from-env-var",
    "sensitive-host-mounts",
    "ssh-port",
    "unsafe-sysctls",
    "unsafe-proc-mount",
}

_CHARACTERISTIC_NAMES_JA = {
    "reliability": "信頼性",
    "performance": "性能効率性",
    "safety": "安全性",
}


class KubeLinterUnavailable(RuntimeError):
    """kube-linter 収集をスキップすべき状況 (対象パス無し・バイナリ無し・実行失敗)."""


def _characteristic_of(check: str) -> str:
    if check in _PERFORMANCE_CHECKS:
        return "performance"
    if check in _SAFETY_CHECKS:
        return "safety"
    return "reliability"


def _run_lint(path: str, kubelinter_bin: str) -> dict[str, Any]:
    if not os.path.isdir(path):
        raise KubeLinterUnavailable(
            f"対象パスがありません: {path} (リポジトリ未 clone? FORGEJO_PAT 未投入?)"
        )
    cmd = [
        kubelinter_bin,
        "lint",
        "--format",
        "json",
        "--include",
        ",".join(EXTRA_CHECKS),
        path,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError as e:
        raise KubeLinterUnavailable(
            f"kube-linter バイナリが見つかりません: {kubelinter_bin}"
        ) from e
    # 仕様: 指摘なし=0, 指摘あり=非ゼロ。それ以外の失敗は stdout が JSON にならない
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        stderr_head = (proc.stderr or "").strip().splitlines()[:3]
        raise KubeLinterUnavailable(
            f"lint 出力をパースできません (exit={proc.returncode}): "
            + " / ".join(stderr_head)
        ) from e


def _count_yaml_files(path: str) -> int:
    n = 0
    for _root, _dirs, files in os.walk(path):
        n += sum(1 for f in files if f.endswith((".yaml", ".yml")))
    return n


def collect(
    *,
    path: str = "manifests",
    kubelinter_bin: str = "kube-linter",
    from_json: str | None = None,
) -> list[Metric]:
    """lint 結果を特性別に集計して Metric のリストを返す."""
    if from_json:
        with open(from_json, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = _run_lint(path, kubelinter_bin)

    if "Reports" not in data:
        # 想定スキーマでない出力を「指摘ゼロ」と誤読しないための防御
        raise KubeLinterUnavailable("lint 出力に Reports キーがありません")

    # Go の nil スライスは null になるため、指摘ゼロは Reports: null で正常
    reports = data.get("Reports") or []

    per_check: dict[str, int] = {}
    for r in reports:
        check = r.get("Check", "")
        if check:
            per_check[check] = per_check.get(check, 0) + 1

    totals = {c: 0 for c in _CHARACTERISTIC_NAMES_JA}
    metrics: list[Metric] = []
    for check, count in sorted(per_check.items()):
        char = _characteristic_of(check)
        totals[char] += count
        metrics.append(
            Metric(
                id=f"lint.check.{check}",
                characteristic=char,
                subcharacteristic=None,
                name=f"kube-linter: {check}",
                value=float(count),
                unit="count",
                source="kubelinter",
                labels={"check": check},
            )
        )

    for char, total in totals.items():
        metrics.append(
            Metric(
                id=f"{char}.lint.issues",
                characteristic=char,
                name=f"kube-linter 指摘件数 ({_CHARACTERISTIC_NAMES_JA[char]})",
                value=float(total),
                unit="count",
                source="kubelinter",
            )
        )

    # 検査対象の YAML ファイル数 = サンプル数 (insufficient data 判定に使う)。
    # from_json 経路では対象パスを読めないため省略する (テスト時は不要)。
    if not from_json:
        metrics.append(
            Metric(
                id="lint.files",
                characteristic="reliability",
                name="lint 対象 YAML ファイル数",
                value=float(_count_yaml_files(path)),
                unit="count",
                source="kubelinter",
            )
        )
    return metrics
