"""Ollama (ローカル LLM) でドキュメント群の相互作用性を評価する collector.

ISO/IEC 25010:2023 の相互作用性 (interaction capability) のうち、本リポジトリの
ユーザー接面である運用ドキュメント (README + docs/**/*.md) を対象に、
自己記述性 / ユーザアシスタンス / 適切性識別性 の 3 副特性を LLM-as-judge で
0-100 採点する (docs/quality-model.md 相互作用性の行。「意味理解が必要な箇所
のみ Ollama を使う」方針の適用例)。

判定の安定化のため Ollama の structured outputs (`format` に JSON Schema) と
`think: false` (qwen3 系の思考モード抑止) と temperature 0 を併用する。
LLM 採点は絶対値でなく時系列の相対変化を見る前提 (quality-model.md)。モデルを
差し替えると基準線がズレるため、全メトリクスの labels にモデル名を記録する。

データ取得は他 collector と同じ 2 経路:
- デフォルト: ollama-external ConfigMap 由来の URL へ /api/chat を直接叩く
- `from_json`: 事前保存した評価結果 JSON から読む (オフライン検証・試験性)。
  形式: {"model": "...", "evaluations": [{"doc": "...", "self_descriptiveness": n,
  "user_assistance": n, "appropriateness_recognizability": n}, ...]}

URL 未設定・接続不可・対象ドキュメント無しは OllamaUnavailable を上げ、
呼び出し側 (cli) が警告してこの source だけスキップする (sonarqube と同パターン。
Windows ホスト側 Ollama が停止していても nightly の他 collector を止めない)。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

from ..models import Metric

DEFAULT_MODEL = "qwen3:14b"

# 1 文書あたり LLM に渡す上限文字数。qwen3 の日本語はおおむね 1 文字 1 トークン
# 前後なので、num_ctx 16384 に対しプロンプト込みで余裕を持つ値にする。
MAX_DOC_CHARS = 8000

# 1 回の収集で評価する文書数の上限 (nightly の実行時間の暴走防止)
MAX_DOCS = 20

_REQUEST_TIMEOUT = 180  # 秒。コールドスタート時のモデルロードを見込む

# 副特性キー -> (Metric id, 人間可読名)
_SUBCHARS: dict[str, tuple[str, str]] = {
    "self_descriptiveness": (
        "interaction.doc.self_descriptiveness",
        "ドキュメント自己記述性 (平均)",
    ),
    "user_assistance": (
        "interaction.doc.user_assistance",
        "ドキュメントユーザアシスタンス (平均)",
    ),
    "appropriateness_recognizability": (
        "interaction.doc.appropriateness",
        "ドキュメント適切性識別性 (平均)",
    ),
}

# Ollama structured outputs に渡す JSON Schema。生成がこの形に制約されるため
# 出力は常にパース可能になる (値域はモデルが守らない可能性に備え後段で clamp)。
_EVAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "self_descriptiveness": {"type": "integer", "minimum": 0, "maximum": 100},
        "user_assistance": {"type": "integer", "minimum": 0, "maximum": 100},
        "appropriateness_recognizability": {
            "type": "integer",
            "minimum": 0,
            "maximum": 100,
        },
        "comment": {"type": "string"},
    },
    "required": [
        "self_descriptiveness",
        "user_assistance",
        "appropriateness_recognizability",
    ],
}

_SYSTEM_PROMPT = (
    "あなたはソフトウェア品質評価者です。ISO/IEC 25010:2023 の相互作用性"
    " (interaction capability) の観点で、インフラ運用ドキュメントを厳密かつ"
    "一貫した基準で採点します。出力は指定された JSON のみ。"
)

_RUBRIC = """\
以下の運用ドキュメントを 3 観点で 0-100 の整数で採点してください。

- self_descriptiveness (自己記述性): 文書自身が目的・前提・対象読者を冒頭で説明しているか
- user_assistance (ユーザアシスタンス): 手順が具体的で実行可能か。結果の確認方法や失敗時の対処が示されているか
- appropriateness_recognizability (適切性識別性): タイトルと冒頭から「いつこの文書を読むべきか」を即座に判断できるか

採点基準: 90-100=模範的 / 70-89=良好 / 50-69=改善余地あり / 30-49=不十分 / 0-29=ほぼ機能していない
"""


class OllamaUnavailable(RuntimeError):
    """Ollama 収集をスキップすべき状況 (URL 未設定・接続不可・対象文書無し)."""


def _discover_docs(root: str) -> list[str]:
    """README.md と docs/**/*.md を決定的な順序で列挙する (root からの相対パス)."""
    if not os.path.isdir(root):
        raise OllamaUnavailable(
            f"対象パスがありません: {root} (リポジトリ未 clone? FORGEJO_PAT 未投入?)"
        )
    rels: list[str] = []
    if os.path.isfile(os.path.join(root, "README.md")):
        rels.append("README.md")
    docs_dir = os.path.join(root, "docs")
    for dirpath, dirs, files in os.walk(docs_dir):
        dirs.sort()
        for f in sorted(files):
            if f.endswith(".md"):
                rels.append(os.path.relpath(os.path.join(dirpath, f), root))
    if not rels:
        raise OllamaUnavailable(f"評価対象の Markdown がありません: {root}")
    if len(rels) > MAX_DOCS:
        print(
            f"warning: 対象 {len(rels)} 件中、先頭 {MAX_DOCS} 件のみ評価します",
            file=sys.stderr,
        )
        rels = rels[:MAX_DOCS]
    return rels


def _chat(base_url: str, payload: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        # 404 はモデル未 pull。接続そのものの問題として収集全体を諦める
        detail = e.read().decode("utf-8", errors="replace")[:200]
        raise OllamaUnavailable(f"Ollama API エラー (HTTP {e.code}): {detail}") from e
    except urllib.error.URLError as e:
        raise OllamaUnavailable(
            f"Ollama に接続できません: {e.reason} (Windows ホスト側が停止中?)"
        ) from e


def _evaluate_doc(base_url: str, model: str, rel: str, text: str) -> dict[str, Any]:
    """1 文書を採点し {"doc": rel, <副特性キー>: int, ...} を返す."""
    truncated = len(text) > MAX_DOC_CHARS
    body = text[:MAX_DOC_CHARS]
    note = "\n(注: 長文のため冒頭のみを評価対象とする)" if truncated else ""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"{_RUBRIC}\n# 対象: {rel}{note}\n\n{body}",
            },
        ],
        "stream": False,
        "think": False,
        "format": _EVAL_SCHEMA,
        "options": {"temperature": 0, "num_ctx": 16384},
    }
    data = _chat(base_url, payload)
    content = data.get("message", {}).get("content", "")
    result = _parse_eval(content)
    result["doc"] = rel
    return result


def _parse_eval(content: str) -> dict[str, Any]:
    """モデル出力 (JSON 文字列) を検証付きで dict に変換する."""
    try:
        raw = json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(f"モデル出力が JSON ではありません: {content[:120]!r}") from e
    out: dict[str, Any] = {}
    for key in _SUBCHARS:
        try:
            v = float(raw[key])
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError(f"採点キー {key} がありません/数値ではありません") from e
        out[key] = max(0.0, min(100.0, v))
    return out


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def collect(
    *,
    docs_path: str = ".",
    base_url: str | None = None,
    model: str = DEFAULT_MODEL,
    from_json: str | None = None,
) -> list[Metric]:
    """ドキュメント評価を集計して Metric のリストを返す."""
    if from_json:
        with open(from_json, "r", encoding="utf-8") as f:
            data = json.load(f)
        model = data.get("model", model)
        evaluations = data.get("evaluations", [])
        if not evaluations:
            raise OllamaUnavailable("from_json に evaluations がありません")
    else:
        if not base_url:
            raise OllamaUnavailable(
                "Ollama の URL が未設定です (OLLAMA_BASE_URL / ollama-external ConfigMap 未投入?)"
            )
        evaluations = []
        for rel in _discover_docs(docs_path):
            with open(os.path.join(docs_path, rel), "r", encoding="utf-8") as f:
                text = f.read()
            try:
                evaluations.append(_evaluate_doc(base_url, model, rel, text))
            except ValueError as e:
                # 単一文書の採点失敗は飛ばして続行 (全滅なら下で unavailable)
                print(f"warning: {rel} の評価をスキップ: {e}", file=sys.stderr)
        if not evaluations:
            raise OllamaUnavailable("全ドキュメントの評価に失敗しました")

    labels = {"model": model}
    metrics: list[Metric] = []

    # 文書別スコア (3 観点の平均)。doc ラベルで個別に追える粒度を残す
    per_doc: list[tuple[str, float]] = []
    for ev in evaluations:
        rel = str(ev.get("doc", "unknown"))
        doc_score = _mean([float(ev[k]) for k in _SUBCHARS])
        per_doc.append((rel, doc_score))
        metrics.append(
            Metric(
                id=f"doc.score.{rel}",
                characteristic="interaction",
                name=f"ドキュメント評価: {rel}",
                value=round(doc_score, 1),
                unit="score",
                source="ollama",
                labels={**labels, "doc": rel},
            )
        )

    # 副特性別の平均 (スコアラの主入力)
    for key, (metric_id, name) in _SUBCHARS.items():
        metrics.append(
            Metric(
                id=metric_id,
                characteristic="interaction",
                subcharacteristic=key.replace("_", "-"),
                name=name,
                value=round(_mean([float(ev[key]) for ev in evaluations]), 1),
                unit="score",
                source="ollama",
                labels=dict(labels),
            )
        )

    worst_doc, worst = min(per_doc, key=lambda t: t[1])
    metrics.append(
        Metric(
            id="interaction.doc.min_score",
            characteristic="interaction",
            name="最低評価ドキュメントのスコア",
            value=round(worst, 1),
            unit="score",
            source="ollama",
            labels={**labels, "doc": worst_doc},
        )
    )
    # 評価済み文書数 = サンプル数 (insufficient data 判定に使う)
    metrics.append(
        Metric(
            id="interaction.docs.evaluated",
            characteristic="interaction",
            name="評価済みドキュメント数",
            value=float(len(evaluations)),
            unit="count",
            source="ollama",
            labels=dict(labels),
        )
    )
    return metrics
