# quality-agent

ISO/IEC 25010:2023 (製品品質) + 25019:2023 (利用時の品質) に基づく自動品質評価エージェント。
Forgejo Actions から `collect → analyze → score → report` のパイプラインとして呼び出す。
設計の全体像は [docs/quality-model.md](../docs/quality-model.md) を参照。

## 現状 (Phase 4)

実装済みの特性は 8 つ:

- **security**: Trivy の VulnerabilityReport から severity 別 CVE 件数を集計
  (加重 CVE 数を log 正規化) + Macaron の SLSA 監査 check 通過率 (linear 正規化)。
  2 サブスコアの平均。Macaron は Forgejo を git service としてサポートしない
  ため、`[git_service.local_repo]` エスケープハッチで clone 済みリポジトリを
  ローカル解析する (nightly Job の macaron initContainer。GITHUB_TOKEN は
  起動時の非空チェック用ダミーで可)。レポート JSON 名は origin URL 由来で
  固定できないため、collector はディレクトリを再帰探索する。
- **maintainability**: SonarQube Web API (`api/measures/component`) から
  技術的負債比・重複行密度ほかを取得 → linear 正規化の平均。解析自体は
  `sonar-scan-nightly` CronJob が毎晩実行する
  ([docs/runbooks/sonarqube-scan.md](../docs/runbooks/sonarqube-scan.md))。
- **reliability / performance / safety**: イメージ同梱の kube-linter で
  リポジトリの `manifests/` を検査し、check 名を特性にマッピングして件数を
  linear 正規化 (probe 未設定 → reliability、resource 未設定 → performance、
  securityContext 系 → safety)。safety は CoU 重み 0 のため記録のみ。
- **interaction**: README + `docs/**/*.md` を Windows ネイティブ Ollama
  (`ollama-external` ConfigMap 経由) の LLM-as-judge で採点。自己記述性・
  ユーザアシスタンス・適切性識別性を 0-100 で評価し平均する。structured
  outputs (`format` に JSON Schema) + `think: false` + temperature 0 で
  出力を安定化。モデル名は全メトリクスの labels に記録する (差し替え時の
  基準線ズレを時系列上で識別するため)。
- **beneficialness / acceptability** (25019 QiU): Forgejo API の Issue/PR
  メタデータから Issue クローズ率・クローズ/マージ所要中央日数・90 日滞留
  Issue を集計。少サンプル時は insufficient data として総合から除外する。

スコアは CoU 重み付きで総合化し、nightly CronJob が pg-main の quality DB に
時系列保存する。PR コメント (Forgejo Actions) は今後の肉付けで追加する
(本 README 末尾「今後」)。

## 設計方針

- **依存最小**: 本体は標準ライブラリ + PyYAML のみ。in-cluster コンテナを軽量に保ち、
  可搬性・保守性を上げる。pydantic 等は使わず dataclass + 手書き JSON round-trip。
- **疎結合パイプライン**: 各サブコマンドは JSON ファイルを介して繋がる。Forgejo
  Actions の job がこれらを順に呼ぶ。
- **ルールベース優先**: 機械的に取れる指標はルールで取り、意味理解が要る箇所
  (ドキュメント品質の採点など) のみ `ollama-external` 経由の LLM に委譲する。
- **しきい値は外出し**: `policies/<project>.yaml` (しきい値・重み) と
  `contexts/<project>.yaml` (利用コンテキスト / CoU) を Git 管理する。

## パッケージ構成

```
quality-agent/
├── pyproject.toml              メタデータ + PyYAML 依存 + entry point
├── Dockerfile                  python:3.12-slim + kubectl 同梱
├── quality_agent/
│   ├── cli.py                  argparse: collect/analyze/score/report
│   ├── models.py               dataclass: Metric/Collection/Analysis/ScoreReport
│   ├── config.py               Policy / Context (YAML) ローダ
│   ├── collectors/trivy.py     VulnerabilityReport 集計 (kubectl / --from-json)
│   ├── collectors/sonarqube.py SonarQube 保守性メジャー取得 (urllib / --sonarqube-from-json)
│   ├── collectors/kubelinter.py kube-linter 実行 + 特性マッピング (--kubelinter-from-json)
│   ├── collectors/ollama.py    ドキュメント LLM 評価 (urllib / --ollama-from-json)
│   ├── analysis/rules.py       ルールベース Finding
│   ├── scoring/normalize.py    linear / logarithmic / binary
│   ├── scoring/score.py        特性別スコア + CoU 重み付き総合
│   └── report/render.py        stdout (text / json)
├── policies/default.yaml       severity 重み・しきい値
├── contexts/home-monitoring-app.yaml  利用コンテキスト (CoU)
└── tests/test_scoring.py       純ロジックの unittest (PyYAML/kubectl 不要)
```

## ローカル実行 (WSL 内)

クラスタが稼働し trivy-operator が VulnerabilityReport を生成済みであること。

```bash
cd /mnt/c/Users/sakai/Main/repos/local-infra/quality-agent

# 1. 収集 (直 kubectl 経路。--sources で collector を選ぶ。既定は trivy のみ)
python3 -m quality_agent collect --target local-infra \
    --sources trivy,sonarqube --out /tmp/collection.json
#    sonarqube は SONARQUBE_TOKEN env が必要 (未設定なら warning を出してスキップ)。
#    WSL からは SONARQUBE_URL に port-forward 先などを指定する。
#    ollama は OLLAMA_BASE_URL (例 http://192.168.0.50:11434) と
#    --ollama-docs-path にリポジトリルートを指定する (WSL からは .. で可)。

#    オフライン検証する場合は事前に JSON を保存して --from-json で食わせる
kubectl get vulnerabilityreports.aquasecurity.github.io -A -o json > /tmp/trivy.json
python3 -m quality_agent collect --from-json /tmp/trivy.json --out /tmp/collection.json

# 2. 解析 (ルールベース Finding 付与)
python3 -m quality_agent analyze --in /tmp/collection.json --out /tmp/analysis.json

# 3. スコアリング
python3 -m quality_agent score --in /tmp/analysis.json \
    --context contexts/home-monitoring-app.yaml \
    --policy policies/default.yaml \
    --out /tmp/scores.json

# 4. レポート出力 (text / json)
python3 -m quality_agent report --in /tmp/scores.json --analysis /tmp/analysis.json
```

## テスト

```bash
python3 -m unittest discover -s tests
```

## コンテナビルド

```bash
docker build -t quality-agent:0.1.0 quality-agent/
```

## スコアの読み方

絶対値の高低そのものより **時系列の相対変化** を見る前提で設計している。
セキュリティスコアはベースイメージ由来の既知 CVE が累積するため低めに出る
(k3d 全 ns スキャンの加重 CVE 数は数万規模)。しきい値はプロジェクト単位で
`policies/<project>.yaml` を調整する。モデル差し替え時は基準線がズレる点に注意
(docs/quality-model.md「NLP 評価で気をつけること」)。

## 今後 (肉付け順の目安)

1. ~~**PostgreSQL 保存**~~: 済 (`report --to-postgres`、nightly CronJob 稼働中)。
2. ~~**SonarQube collector**~~: 済 (maintainability。有効化は [runbook](../docs/runbooks/sonarqube-scan.md))。
3. ~~**kube-linter collector**~~: 済 (reliability/performance/safety)。
4. ~~**Ollama 連携 (interaction)**~~: 済 (ドキュメント品質の LLM-as-judge 採点)。
   要件↔テスト整合・Issue sentiment などの意味理解系は Forgejo メタデータ
   collector とあわせて拡張する。
5. ~~**Forgejo メタデータ collector** (25019)~~: 済 (beneficialness/acceptability。
   Issue クローズ率・クローズ/マージ所要中央日数・90 日滞留 Issue)。
6. ~~**Macaron 投入** (Phase 3 から延期)~~: 済 (SLSA 監査。security の耐性サブ特性)。
7. **Forgejo Actions ワークフロー**: push/PR トリガで本パイプラインを実行し PR にコメント。
