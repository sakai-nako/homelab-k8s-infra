"""quality-agent CLI. collect -> analyze -> score -> report のパイプライン.

各サブコマンドは JSON ファイルを介して疎結合に繋がる。Forgejo Actions の job は
これらを順に呼び出す (docs/quality-model.md パイプライン参照)。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from . import __version__
from .analysis import rules
from .collectors import forgejo, kubelinter, macaron, ollama, sonarqube, trivy
from .config import Context, Policy
from .models import Analysis, Collection
from .report import render
from .scoring import score as scoring
from .util import now_iso

KNOWN_SOURCES = ("trivy", "sonarqube", "kubelinter", "ollama", "forgejo", "macaron")


def _write_json(obj: dict[str, Any], path: str | None) -> None:
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    if path:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    else:
        print(text)


def _read_json(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def cmd_collect(args: argparse.Namespace) -> int:
    requested = [s.strip() for s in args.sources.split(",") if s.strip()]
    unknown = [s for s in requested if s not in KNOWN_SOURCES]
    if unknown:
        print(
            f"error: 未知の source です: {', '.join(unknown)} "
            f"(指定可能: {', '.join(KNOWN_SOURCES)})",
            file=sys.stderr,
        )
        return 2

    metrics = []
    collected: list[str] = []

    if "trivy" in requested:
        metrics += trivy.collect(
            from_json=args.from_json,
            namespace=args.namespace,
            kubectl_bin=args.kubectl,
        )
        collected.append("trivy")

    if "sonarqube" in requested:
        try:
            metrics += sonarqube.collect(
                component=args.sonarqube_component or args.target,
                base_url=args.sonarqube_url,
                token=os.environ.get("SONARQUBE_TOKEN"),
                from_json=args.sonarqube_from_json,
            )
            collected.append("sonarqube")
        except sonarqube.SonarQubeUnavailable as e:
            # token 未投入や未解析で nightly 全体を落とさない (他 source は保存する)。
            # 欠落は score 段の notes と postgres 上の特性欠落として可視化される。
            print(f"warning: sonarqube collector をスキップ: {e}", file=sys.stderr)

    if "kubelinter" in requested:
        try:
            metrics += kubelinter.collect(
                path=args.kubelinter_path,
                kubelinter_bin=args.kubelinter_bin,
                from_json=args.kubelinter_from_json,
            )
            collected.append("kubelinter")
        except kubelinter.KubeLinterUnavailable as e:
            # clone 用 PAT 未投入 (対象パス無し) 等。sonarqube と同じスキップ方針
            print(f"warning: kubelinter collector をスキップ: {e}", file=sys.stderr)

    if "forgejo" in requested:
        try:
            metrics += forgejo.collect(
                repo=args.forgejo_repo,
                base_url=args.forgejo_url,
                token=os.environ.get("FORGEJO_TOKEN"),
                from_json=args.forgejo_from_json,
            )
            collected.append("forgejo")
        except forgejo.ForgejoUnavailable as e:
            # PAT 未投入・スコープ不足 (read:issue) で nightly 全体を落とさない
            print(f"warning: forgejo collector をスキップ: {e}", file=sys.stderr)

    if "macaron" in requested:
        try:
            metrics += macaron.collect(report_path=args.macaron_report)
            collected.append("macaron")
        except macaron.MacaronUnavailable as e:
            # macaron initContainer の失敗/スキップで nightly 全体を落とさない
            print(f"warning: macaron collector をスキップ: {e}", file=sys.stderr)

    if "ollama" in requested:
        try:
            metrics += ollama.collect(
                docs_path=args.ollama_docs_path,
                base_url=args.ollama_url,
                model=args.ollama_model,
                from_json=args.ollama_from_json,
            )
            collected.append("ollama")
        except ollama.OllamaUnavailable as e:
            # Windows ホスト側 Ollama の停止や ConfigMap 未投入で nightly 全体を
            # 落とさない (sonarqube / kubelinter と同じスキップ方針)
            print(f"warning: ollama collector をスキップ: {e}", file=sys.stderr)

    collection = Collection(
        collected_at=now_iso(),
        target=args.target,
        metrics=metrics,
        sources=collected,
    )
    _write_json(collection.to_dict(), args.out)
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    collection = Collection.from_dict(_read_json(args.in_path))
    analysis = rules.analyze(collection)
    _write_json(analysis.to_dict(), args.out)
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    analysis = Analysis.from_dict(_read_json(args.in_path))
    context = Context.load(args.context)
    policy = Policy.load(args.policy)
    report = scoring.score(analysis, context, policy)
    _write_json(report.to_dict(), args.out)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    from .models import ScoreReport

    report = ScoreReport.from_dict(_read_json(args.in_path))
    analysis = None
    if args.analysis:
        analysis = Analysis.from_dict(_read_json(args.analysis))

    if args.to_postgres:
        from .report import postgres

        run_id = postgres.write(report, dsn=args.dsn)
        print(f"saved to postgres: run_id={run_id}")

    if args.format == "json":
        print(render.render_json(report))
    else:
        print(render.render_text(report, analysis))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="quality-agent",
        description="ISO/IEC 25010:2023 + 25019:2023 自動品質評価エージェント",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    # collect
    c = sub.add_parser("collect", help="ツール出力を Metric に正規化して収集する")
    c.add_argument("--target", default="local-infra", help="評価対象の識別子")
    c.add_argument(
        "--sources",
        default="trivy",
        help=f"カンマ区切りの collector 指定 (指定可能: {', '.join(KNOWN_SOURCES)})",
    )
    c.add_argument(
        "--from-json",
        dest="from_json",
        default=None,
        help="kubectl の代わりに VulnerabilityReport JSON ファイルから読む",
    )
    c.add_argument("--namespace", default=None, help="対象 namespace (既定: 全 ns)")
    c.add_argument("--kubectl", default="kubectl", help="kubectl バイナリパス")
    c.add_argument(
        "--sonarqube-url",
        default=os.environ.get("SONARQUBE_URL", sonarqube.DEFAULT_URL),
        help="SonarQube ベース URL (既定: env SONARQUBE_URL / in-cluster Service)",
    )
    c.add_argument(
        "--sonarqube-component",
        default=None,
        help="SonarQube プロジェクトキー (既定: --target と同値)",
    )
    c.add_argument(
        "--sonarqube-from-json",
        dest="sonarqube_from_json",
        default=None,
        help="API の代わりに api/measures/component レスポンス JSON から読む",
    )
    c.add_argument(
        "--kubelinter-path",
        default="manifests",
        help="kube-linter の検査対象ディレクトリ (既定: manifests)",
    )
    c.add_argument(
        "--kubelinter-bin", default="kube-linter", help="kube-linter バイナリパス"
    )
    c.add_argument(
        "--kubelinter-from-json",
        dest="kubelinter_from_json",
        default=None,
        help="実行する代わりに lint --format json の出力ファイルから読む",
    )
    c.add_argument(
        "--forgejo-url",
        default=os.environ.get("FORGEJO_URL", forgejo.DEFAULT_URL),
        help="Forgejo ベース URL (既定: env FORGEJO_URL / in-cluster Service)",
    )
    c.add_argument(
        "--forgejo-repo",
        default=os.environ.get("FORGEJO_REPO", forgejo.DEFAULT_REPO),
        help=f"Issue/PR を読む owner/repo (既定: env FORGEJO_REPO / {forgejo.DEFAULT_REPO})",
    )
    c.add_argument(
        "--forgejo-from-json",
        dest="forgejo_from_json",
        default=None,
        help='API の代わりに {"issues": [...], "pulls": [...]} 形式の JSON から読む',
    )
    c.add_argument(
        "--macaron-report",
        dest="macaron_report",
        default=None,
        help=(
            "Macaron レポート JSON のパスまたは探索ルートディレクトリ "
            "(レポート名は origin URL 由来で固定できないためディレクトリ推奨)"
        ),
    )
    c.add_argument(
        "--ollama-url",
        default=os.environ.get("OLLAMA_BASE_URL"),
        help="Ollama ベース URL (既定: env OLLAMA_BASE_URL。未設定なら skip)",
    )
    c.add_argument(
        "--ollama-model",
        default=os.environ.get("OLLAMA_MODEL", ollama.DEFAULT_MODEL),
        help=f"Ollama モデル名 (既定: env OLLAMA_MODEL / {ollama.DEFAULT_MODEL})",
    )
    c.add_argument(
        "--ollama-docs-path",
        default=".",
        help="ドキュメント評価対象のリポジトリルート (既定: カレント)",
    )
    c.add_argument(
        "--ollama-from-json",
        dest="ollama_from_json",
        default=None,
        help="API の代わりに事前保存した評価結果 JSON から読む",
    )
    c.add_argument("--out", default=None, help="出力 JSON パス (既定: stdout)")
    c.set_defaults(func=cmd_collect)

    # analyze
    a = sub.add_parser("analyze", help="ルールベースで Finding を導く")
    a.add_argument("--in", dest="in_path", required=True, help="collect 出力 JSON")
    a.add_argument("--out", default=None, help="出力 JSON パス (既定: stdout)")
    a.set_defaults(func=cmd_analyze)

    # score
    s = sub.add_parser("score", help="特性別 + CoU 重み付き総合スコアを算出する")
    s.add_argument("--in", dest="in_path", required=True, help="analyze 出力 JSON")
    s.add_argument("--context", required=True, help="利用コンテキスト YAML")
    s.add_argument("--policy", required=True, help="ポリシ (しきい値) YAML")
    s.add_argument("--out", default=None, help="出力 JSON パス (既定: stdout)")
    s.set_defaults(func=cmd_score)

    # report
    r = sub.add_parser("report", help="ScoreReport を出力する")
    r.add_argument("--in", dest="in_path", required=True, help="score 出力 JSON")
    r.add_argument(
        "--analysis", default=None, help="analyze 出力 JSON (所見を併記する場合)"
    )
    r.add_argument(
        "--format", choices=["text", "json"], default="text", help="出力形式"
    )
    r.add_argument(
        "--to-postgres",
        dest="to_postgres",
        action="store_true",
        help="ScoreReport を pg-main の quality DB に時系列保存する (psycopg 必須)",
    )
    r.add_argument(
        "--dsn",
        default=None,
        help="libpq DSN/URI (既定: 環境変数 QUALITY_AGENT_DSN / PG* に委ねる)",
    )
    r.set_defaults(func=cmd_report)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as e:
        print(f"error: ファイルが見つかりません: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001  skeleton 段では最上位で握り潰す
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
