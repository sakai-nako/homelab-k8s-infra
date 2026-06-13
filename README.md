# local-infra

> [!NOTE]
> このリポジトリは、自宅の Forgejo で GitOps 運用しているプライベートリポジトリの**フィルタ済み公開ミラー**です (生成元コミット: `bba5bcd`)。
> SealedSecret 暗号文 (`secrets/*.yaml`) の除外と LAN 固有値の例示値への置換を行ったスナップショットを、
> [tools/publish-public-mirror.ps1](tools/publish-public-mirror.ps1) で随時 push しています。
> ArgoCD が同期する正本はプライベート側にあるため、運用の実コミット履歴はここには含まれません。
Windows + WSL2 + k3d 上に構築する、**個人向けローカル完結型 ソフトウェア品質評価プラットフォーム**。

ISO/IEC 25010:2023 (製品品質モデル) および ISO/IEC 25019:2023 (利用時の品質モデル) に基づき、Gitリポジトリ・コード・運用メタデータを自動解析する AI エージェントを、自宅環境内で運用する。

> 規格・理論的根拠の原典: [docs/iso-quality-framework.md](docs/iso-quality-framework.md)

---

## 目的

1. **品質評価フレームワークの実用化**: [docs/iso-quality-framework.md](docs/iso-quality-framework.md) で示された ISO/IEC 25010:2023 / 25019:2023 の自動評価フレームワークを、外部クラウドに依存せず個人で実運用できる形に落とす。
2. **k8s 実運用経験の獲得**: GitOps (ArgoCD App-of-Apps) を中核に据え、宣言的に管理可能なクラスタ運用ノウハウを蓄積する。
3. **可搬性**: Windows マシンを買い替えても、Forgejo に置いた本リポジトリ + NAS 上のバックアップから完全復元できること。

---

## 設計三原則

| 原則 | 意図 |
| :--- | :--- |
| **Local-first** | クラウド依存ゼロ。ローカル LLM (Ollama) も含め完結する。 |
| **Declarative & GitOps** | クラスタ状態は Git が唯一の真実。手動 `kubectl apply` は原則禁止 (bootstrap のみ例外)。 |
| **Recoverable** | 全状態が「Git にある構成」+「NAS の論理ダンプ」から復元可能。 |

---

## スタックサマリ

| レイヤ | コンポーネント | 役割 |
| :--- | :--- | :--- |
| ホスト | Windows 11 + WSL2 (Ubuntu) + Docker Engine | ベース環境 |
| クラスタ | **k3d** (Docker-in-Docker で k3s) | マルチノードシミュレーション |
| GitOps | **ArgoCD** (App-of-Apps) | 全コンポーネントの宣言的管理 |
| Git ホスト | **Forgejo** | リポジトリ + コンテナレジストリ (Runner は不採用、定期処理は CronJob 方式) |
| Ingress | Traefik (k3d 同梱) | HTTP ルーティング |
| Secret 管理 | **Sealed Secrets** | 暗号文を Git にコミット可能化 |
| RDB | **CloudNativePG** (operator + `pg-main` Cluster) | Forgejo/SonarQube/Grafana/quality のバックエンド |
| 監視 | **kube-prometheus-stack** (Prometheus + Grafana + Alertmanager) | メトリクス・ダッシュボード・品質劣化アラート |
| 静的解析 | SonarQube CE / kube-linter | ISO 25010 保守性・信頼性・性能・安全性 |
| サプライチェーン | Trivy (trivy-operator) / Macaron | ISO 25010 セキュリティ (CVE + SLSA) |
| ローカル LLM | **Ollama** (Windows ネイティブ / Qwen / Gemma / 日本語特化モデル, 後から差し替え可) | ISO 25019 利用時の品質 NLP 評価。GPU 直結のため k3d 外で稼働しクラスタからは HTTP 接続 |
| 評価エージェント | 自作 Python パイプライン (`quality-agent/`) | スコアリング・レポート生成 |
| バックアップ Obj | **SeaweedFS** (S3 互換) | Velero バックエンド |
| バックアップ運用 | Velero + 論理ダンプ CronJob | 2 層バックアップ |
| ストレージ宛先 | NAS (WSL2 から SMB マウント) | 最終バックアップ先 |

詳細とコンポーネント間関係は [docs/architecture.md](docs/architecture.md) を参照。

---

## リポジトリ構成

```
local-infra/
├── README.md
├── sonar-project.properties      SonarQube 解析設定 (プロジェクトキー local-infra)
├── docs/
│   ├── iso-quality-framework.md  ISO/IEC 25010/25019 規格と理論的根拠 (原典)
│   ├── architecture.md           アーキテクチャ詳細
│   ├── quality-model.md          ISO 25010/25019 → 実装マッピング
│   ├── backup.md                 バックアップ戦略 / DR 手順
│   ├── scheduled-jobs.md         定期ジョブ一覧 (1 日のタイムライン)
│   └── runbooks/                 障害対応・運用手順
├── bootstrap/                    初回構築用 (Git 外から手動実行)
│   ├── 00-prerequisites.md       WSL2 / Docker / kubectl / helm / k3d / NAS セットアップ
│   ├── 01-k3d-cluster.yaml       k3d クラスタ定義 (subnet 固定 + レジストリ pull 経路込み)
│   ├── 02-forgejo-install.md     Forgejo の手動 install + 本リポジトリ push
│   ├── 03-argocd-install.md      ArgoCD 手動 install (repoURL は Forgejo 内部 Service)
│   ├── 04-root-app.yaml          App-of-Apps の root Application
│   └── 05-ollama-windows.md      Windows ネイティブ Ollama のセットアップと到達経路
├── clusters/
│   └── home/                     クラスタ単位 (将来増やせる)
│       └── apps/                 子 Application 定義 (1 ファイル = 1 ArgoCD Application)
│           ├── argocd.yaml / forgejo.yaml / postgres.yaml / ...
│           ├── quality-agent.yaml / monitoring-quality.yaml / am-forgejo-bridge.yaml
│           └── backups.yaml / velero-export.yaml / secrets.yaml / ...
├── charts/                       外部 Helm chart に渡す values
│   └── <app>/values.yaml
├── manifests/                    Helm 化されていない自作マニフェスト
│   ├── backups/                  層 1 論理ダンプ + 月次 restore テストの CronJob 群
│   ├── quality-agent/            nightly CronJob ×2 (評価 / SonarQube 解析投入)
│   └── <app>/
├── secrets/                      SealedSecret (暗号文のみ。平文は置かない)
├── quality-agent/                ISO 25010/25019 評価エージェント本体 (Python)
│   ├── quality_agent/            collect / analyze / score / report 実装
│   ├── contexts/                 利用コンテキスト (CoU) 定義 YAML
│   ├── policies/                 プロジェクト別しきい値
│   └── tests/
└── am-forgejo-bridge/            Alertmanager → Forgejo Issue 起票 webhook receiver (Python)
```

---

## 関連ドキュメント

| ドキュメント | 内容 |
| :--- | :--- |
| [docs/iso-quality-framework.md](docs/iso-quality-framework.md) | ISO/IEC 25010:2023 / 25019:2023 規格概説と理論的根拠 (原典) |
| [docs/architecture.md](docs/architecture.md) | 全体構成図、レイヤ別コンポーネント、GitOps 設計、ネットワーク |
| [docs/quality-model.md](docs/quality-model.md) | ISO/IEC 25010:2023 / 25019:2023 → 実装ツール対応、評価フロー |
| [docs/backup.md](docs/backup.md) | 2 層バックアップ戦略、NAS 連携、マシン買い替え時の DR 手順 |
| [docs/scheduled-jobs.md](docs/scheduled-jobs.md) | 定期ジョブ一覧 (1 日のタイムライン、順序依存、TZ の罠) |
| [docs/runbooks/sealed-secrets.md](docs/runbooks/sealed-secrets.md) | sealed-secrets 運用ワークフロー (SealedSecret 化、adopt、Master Key リストア) |
| [docs/runbooks/quality-observability.md](docs/runbooks/quality-observability.md) | 品質スコア観測スタック運用 (ダッシュボード/アラート変更フロー、テスト発火、quality_ro ローテーション) |
| [docs/runbooks/disaster-recovery.md](docs/runbooks/disaster-recovery.md) | 新マシン移行 / クラスタ全損からの完全復旧手順、月次 restore テストと DR dry-run の運用 |
| [docs/runbooks/forgejo-registry.md](docs/runbooks/forgejo-registry.md) | 自作イメージの build / push と k3d からの pull 経路 |
| [docs/runbooks/sonarqube-scan.md](docs/runbooks/sonarqube-scan.md) | SonarQube nightly 解析と quality-agent 連携の有効化 |
| [docs/runbooks/public-mirror.md](docs/runbooks/public-mirror.md) | GitHub 公開ミラーの運用 (フィルタ済みスナップショットの publish 手順) |

---

## 構築フェーズ計画

| フェーズ | 目標 | 完了条件 |
| :--- | :--- | :--- |
| **Phase 0** ✅ | 設計確定 | このドキュメント群が確定し、ユーザー承認済み |
| **Phase 1** ✅ | Bootstrap | WSL2 + k3d + ArgoCD + Forgejo が立ち上がり、本リポジトリが Forgejo に push 済 |
| **Phase 2** ✅ | コア基盤 | PostgreSQL, Sealed Secrets, kube-prometheus-stack, SeaweedFS, Velero が稼働 |
| **Phase 3** ✅ | 品質評価ツール群 | SonarQube, Trivy, Ollama を投入 (Macaron は Phase 4 へ延期) |
| **Phase 4** ✅ | 評価エージェント | `quality-agent/` を実装し、in-cluster nightly CronJob で自動評価 + PostgreSQL 時系列保存 (Macaron もここで投入。当初案の Forgejo Actions は Runner 不要の CronJob 方式に変更) |
| **Phase 5** ✅ | 観測と改善 | Grafana にスコア時系列ダッシュボード、Grafana alerting → Alertmanager で劣化検知、運用 runbook 整備 |
| **Phase 6** ✅ | 通知と復旧訓練 | Alertmanager receiver (am-forgejo-bridge による Forgejo Issue 自動起票)、層 1 論理ダンプ CronJob 群 (forgejo/pg/sealed-secrets 鍵)、disaster-recovery runbook、月次 restore テスト CronJob |

---

## 進捗ステータス

- [x] 構想ドキュメント (temp.md)
- [x] 設計三原則の確定
- [x] スタック選定
- [x] アーキテクチャ図 / GitOps 設計 ([docs/architecture.md](docs/architecture.md))
- [x] 品質評価マッピング ([docs/quality-model.md](docs/quality-model.md))
- [x] バックアップ戦略 ([docs/backup.md](docs/backup.md))
- [x] Phase 1 Bootstrap 実装 (k3d + Forgejo + ArgoCD + App-of-Apps、GitOps 化完了)
- [x] Phase 2 (コア基盤):
  - [x] sealed-secrets / PostgreSQL (CloudNativePG) / SeaweedFS
  - [x] kube-prometheus-stack / Velero (S3→NAS エクスポート含む)
- [x] Phase 3 (品質評価ツール群):
  - [x] SonarQube CE (Community Build、pg-main に外部接続、sonar.local.test)
  - [x] Trivy (trivy-operator、全 ns 継続スキャン + Prometheus メトリクス)
  - [x] ollama-external (Windows ネイティブ Ollama への接続情報 ConfigMap + bootstrap/05 手順書)
  - [x] Windows ホスト側 Ollama 実導入 (qwen3:14b、pod から疎通確認済)
  - [x] クラスタ → Windows の到達 IP を **LAN IP 方式**に確定 (NAT GW / host.k3d.internal / mirrored は検証で塞がり不採用。LAN IP は WSL 再起動耐性あり、DHCP 予約で恒久固定)
  - [→] Macaron は Phase 4 (quality-agent の Forgejo Actions ジョブ) に延期
- [x] Phase 4 (評価エージェント):
  - [x] quality-agent walking skeleton (`collect→analyze→score→report`、security 特性 = Trivy CVE 集計)
  - [x] PostgreSQL 時系列保存 (pg-main の quality DB、`report --to-postgres`)
  - [x] in-cluster nightly CronJob 稼働 (06:00 JST。イメージは Forgejo registry から匿名 pull、経路は [docs/runbooks/forgejo-registry.md](docs/runbooks/forgejo-registry.md))
  - [x] SonarQube collector (maintainability 特性: 技術的負債比・重複行密度。`sonar-scan-nightly` CronJob が毎晩解析、有効化手順は [docs/runbooks/sonarqube-scan.md](docs/runbooks/sonarqube-scan.md))
  - [x] kube-linter collector (reliability/performance/safety 特性: probe・resource 未設定等を `manifests/` から検出。kube-linter はイメージ同梱、リポジトリは nightly Job の initContainer が clone)
  - [x] Ollama collector (interaction 特性: README + docs/**/*.md を LLM-as-judge で採点。自己記述性・ユーザアシスタンス・適切性識別性。これで CoU 重みのある 5 特性が全て揃いカバレッジ 1.0)
  - [x] Forgejo メタデータ collector (25019 QiU: beneficialness/acceptability。Issue クローズ率・クローズ所要中央日数・PR マージ所要中央日数・90 日滞留 Issue を API から集計。PAT は forgejo-read を流用し read:repository + read:issue が必要。少サンプル時は insufficient data 扱い)
  - [x] Macaron collector (security 特性の耐性サブ特性: SLSA 監査の check 通過率。Forgejo は非対応 git service のため `[git_service.local_repo]` エスケープハッチで clone 済みリポジトリをローカル解析。macaron initContainer が `ghcr.io/oracle/macaron` で実行し、security スコアは CVE と SLSA のサブスコア平均に変更)
- [x] Phase 5 (観測と改善):
  - [x] quality_ro 読み取り専用ロール (managed.roles) + Grafana PostgreSQL datasource (uid=`quality-postgres`、password は SealedSecret → `$__env` 注入)
  - [x] Quality Scores ダッシュボード (総合/特性別スコア時系列、最新 run 内訳、run 鮮度。`manifests/monitoring-quality/` の ConfigMap を sidecar provisioning)
  - [x] 劣化検知アラート 4 本 (Grafana-managed: 前回 run 比 overall -5 / 特性 -10、絶対閾値 overall<50、30h 鮮度) → contact point + root policy の provisioning で既存 Alertmanager へ送信
  - [x] 運用 runbook ([docs/runbooks/quality-observability.md](docs/runbooks/quality-observability.md))。E2E 検証済 (劣化 run 注入 → Firing → Alertmanager 到達 → resolve)
  - [→] Alertmanager receiver (Forgejo Issue 起票)・DR runbook・restore テストは Phase 6 へ
- [x] Phase 6 (通知と復旧訓練):
  - [x] am-forgejo-bridge (自作 Python webhook receiver、`am-forgejo-bridge/`): Alertmanager → Forgejo Issue 自動起票。品質劣化アラート (source=quality-agent) とバックアップ系 namespace の KubeJobFailed を起票し、resolve で自動クローズ。「1 アラートグループ = 1 open Issue」を body 内マーカーで dedup、repeat 通知は吸収
  - [x] 層 1 論理ダンプの実装 (`manifests/backups/`、設計のみだったものを実装): forgejo-dump (毎日 03:00 JST)・postgres-dump (pg_dumpall、毎日 03:15 JST、`enableSuperuserAccess: true` 化)・sealed-secrets 鍵の週次 GPG バックアップ (歴代全鍵、ラウンドトリップ検証付き)。Grafana/ArgoCD の個別ダンプは「Git + pg ダンプで再現可能」のため廃止
  - [x] 月次 restore テスト (`backup-restore-test` CronJob、毎月 1 日 07:00 JST): 最新 pg ダンプを使い捨て PostgreSQL に実復元して DB/行数検証 + forgejo zip CRC + 鍵鮮度。失敗は KubeJobFailed → Issue 起票 (通知経路も同時訓練)
  - [x] disaster-recovery runbook ([docs/runbooks/disaster-recovery.md](docs/runbooks/disaster-recovery.md)): 循環依存 (Forgejo→pg-main→ArgoCD→Forgejo) を断ち切る実コマンドベースの完全復旧手順 + 半年ごと dry-run 運用

---

## 注意事項

- 本リポジトリは個人の学習・評価環境用であり、商用品質保証を目的としない。
- ローカル LLM (Ollama) の評価精度はクラウド LLM (Claude/GPT) より劣る。temp.md 文献 [24] のような研究水準の NLP 分析を厳密に再現するものではない。
- **Ollama は k3d / WSL2 の外、Windows ネイティブで動かす** (GPU を直接フル活用し、Windows 側のチャット UI / ComfyUI とも共有するため)。クラスタからは接続情報 ConfigMap (`ollama-external`) を読んで HTTP で叩く。設計詳細は [docs/architecture.md#ollama-windows-ネイティブ-接続](docs/architecture.md)。
- 構築段階で必要な PC リソースの目安: メモリ 32GB / VRAM 8GB 以上推奨。**実運用機は RTX 5080 (16GB) / 64GB RAM** のため、14B 級モデル + フルスタックを余裕をもって同時稼働できる。GPU は Ollama と ComfyUI で共有されるため、両者の同時フル稼働時は VRAM 配分に注意 (時分割前提)。
