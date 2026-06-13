# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## このリポジトリの性質

これは**アプリケーションのコードベースではなく、GitOps で管理する個人向け k8s インフラの宣言的構成リポジトリ**である。ビルド/テスト/lint のパイプラインは無く、成果物は ArgoCD が Forgejo からこのリポジトリを pull して同期する Kubernetes リソース群。目的は ISO/IEC 25010:2023 / 25019:2023 に基づくソフトウェア品質自動評価プラットフォームの自宅運用 (詳細は [README.md](README.md) と [docs/iso-quality-framework.md](docs/iso-quality-framework.md))。

実行環境は **Windows 11 + WSL2 (Ubuntu, `local-infra` ディストリ) + Docker + k3d**。クラスタ操作 (`kubectl` / `helm` / `kubeseal` / `k3d`) は基本 **WSL 内**で行う。Claude Code 自身は Windows 側の PowerShell で動いているため、クラスタを叩くコマンドは `wsl -- ...` 経由になる点に注意 (下記「WSL 越しのコマンド実行」)。

## 設計三原則 (変更時に必ず守る)

1. **Local-first** — クラウド依存ゼロ。repoURL は常に Forgejo のクラスタ内 Service (`http://forgejo-http.forgejo.svc.cluster.local:3000/sakai/local-infra.git`)。GitHub 等の外部を恒久的に参照しない。
2. **Declarative & GitOps** — クラスタ状態は Git が唯一の真実。手動 `kubectl apply` は原則禁止 (例外は `bootstrap/` と、後述の鶏卵問題の打破時のみ)。変更は「Git にコミット → ArgoCD が sync」で反映する。
3. **Recoverable** — 全状態が「Git の構成」+「NAS の論理ダンプ」+「sealed-secrets master key」から復元可能であること。

## ディレクトリ構造とその意味

GitOps の階層が物理ディレクトリに対応している。新しいものを足す場所を間違えないこと。

- `bootstrap/` — **GitOps の輪に入る前**に WSL 内から手動実行する初期化手順 (`00`〜`04`)。ここだけは手動 `kubectl`/`helm` が正規手順。新マシン移行もこの順に踏む。
- `clusters/home/apps/*.yaml` — **App-of-Apps の子 Application 定義**。`bootstrap/04-root-app.yaml` の root Application が `clusters/home/apps/` を `recurse: false` でスキャンし、ファイル 1 つ = ArgoCD Application 1 つを生成する。**アプリを増やす = ここに Application YAML を 1 ファイル足す**。
- `charts/<app>/values.yaml` — 外部 Helm chart に渡す values。対応する `clusters/home/apps/<app>.yaml` が `source.path: charts/<app>` ではなく、chart 本体は `repoURL`/`chart` で OCI または HTTP リポジトリを指し values だけここを参照する形が基本。
- `manifests/<app>/` — Helm 化されていない**自作の生マニフェスト** (CNPG Cluster CR, Job, CronJob 等)。chart Application に生マニフェストは混在できないため、必ず別 Application に分ける。
- `secrets/*.yaml` — **SealedSecret (暗号文)**。`secrets` Application が一括 apply。各ファイルが自身の `metadata.namespace` を持つので 1 Application で複数 namespace に配れる。平文 Secret は絶対にここへ置かない。
- `docs/` — 設計・運用ドキュメント。`docs/runbooks/` は障害対応手順。コードを変える前にここを読むと意図が掴める。

## ArgoCD Application を書くときの確立済みパターン

過去のトラブルから固まった規約。新規 Application 追加時はこれに従う:

- **prune の使い分け**: データを持つ共有リソース (PostgreSQL `cluster.yaml` 等) の Application は `prune: false` にして Git 事故での削除を防ぐ。通常リソースは `prune: true`。
- **chart と生マニフェストは必ず別 Application**: 1 つの Application source に Helm chart と raw manifest を混在できない。`seaweedfs` (chart) と `seaweedfs-buckets` (Job)、`velero` (chart) と `velero-export` (CronJob) のように分割する。
- **Job を Git 管理する場合**: Job は immutable なので `syncOptions: [Replace=true, Force=true]` で毎 sync ごと delete & recreate する。**`Force=true` は必須** — `Replace=true` 単体だと `spec.selector: Required value` で必ず失敗する。また **`Replace` と `ServerSideApply` は併用不可**なので Replace 系では `ServerSideApply` を付けない。hook-only Application は確実に走らないため採用しない。
- **OCI Helm chart の repoURL**: ArgoCD では Helm CLI と異なり、`repoURL` に chart 名まで含む完全パスを書く (例: `code.forgejo.org/forgejo-helm` ではなく chart 名込み)。
- **CNPG `managed.roles`**: operator が `inherit`/`connectionLimit` を defaulting するため、Git 側に明示しないと永遠に OutOfSync になる。明示して drift を消す。

## Secret 運用 (Sealed Secrets)

フローと注意点の完全版は [docs/runbooks/sealed-secrets.md](docs/runbooks/sealed-secrets.md)。要点:

- controller は `sealed-secrets` namespace / `sealed-secrets-controller`。`kubeseal` 実行時はこの ns/name を必ず指定する。
- 平文は `/tmp` に作って `shred -u` で即削除、もしくは pipe で直接 `kubeseal` に食わせる。Git にもディスクにも残さない。
- **master key を失うと全 SealedSecret が永久に復号不能**。NAS に GPG 暗号化バックアップ + パスワードマネージャの二重管理。
- **鶏卵問題**: ArgoCD repo credential (`forgejo-repo`) を SealedSecret 化しているため、PAT 失効時は GitOps で更新できなくなる。このときだけ例外的に `kubectl apply -f secrets/forgejo-repo.yaml` で ArgoCD を bypass して直接投入する (runbook 参照)。

## GitHub 公開ミラー

本リポジトリは `tools/publish-public-mirror.ps1` でフィルタ済みスナップショットを GitHub の public ミラー (https://github.com/sakai-nako/homelab-k8s-infra) へ publish できる (運用は [docs/runbooks/public-mirror.md](docs/runbooks/public-mirror.md))。除外・置換・禁止パターンは `tools/public-mirror-rules.psd1` に集約 (このファイル自体はミラー非公開)。**新規ファイルに LAN の実 IP・ホスト固有値・credential 類を書くときは、公開対象になることを意識し、必要ならルールに追加する**。

## WSL 越しのコマンド実行 (Claude Code 環境固有の罠)

- クラスタ操作は WSL 内のツールで行う。Windows 側からは `wsl -d local-infra -- kubectl ...` のように叩く。
- **`wsl -- bash -lc '...'` の中で `$(...)` コマンド置換が空になる**既知の問題がある。コマンド置換を含むスクリプトは、ヒアドキュメントやインラインではなく**スクリプトファイルに書いてから実行**する。
- CNPG と Velero の CRD 名衝突: `kubectl get backup` は CNPG の `Backup` に解決される。Velero のバックアップは `kubectl get backups.velero.io` と**完全修飾名**で叩く。

## よく使うコマンド

```bash
# (WSL 内) クラスタ起動 — bootstrap 時のみ
k3d cluster create --config bootstrap/01-k3d-cluster.yaml

# Application の状態確認
kubectl -n argocd get applications

# 特定アプリを強制同期 (UI からでも可)
argocd app sync <app-name>

# SealedSecret 作成 (平文を pipe で食わせる)
kubeseal --controller-namespace sealed-secrets \
         --controller-name sealed-secrets-controller \
         --format yaml < /tmp/plain.yaml > secrets/<name>.yaml

# Helm chart のバージョン確認 (OCI)
helm show chart oci://code.forgejo.org/forgejo-helm/forgejo
```

通常の変更反映は `git commit` → `git push origin main`。ArgoCD が 30 秒程度で自動 sync する (`selfHeal: true`)。`origin` は Forgejo を指す事実上唯一のリモート。

## 現在のフェーズ

Phase 4 (評価エージェント) 完了: `quality-agent/` (Python 製、標準ライブラリ + PyYAML) が nightly CronJob で Trivy の CVE + Macaron の SLSA 監査 (security)、SonarQube の保守性メジャー (maintainability)、kube-linter の manifests 検査 (reliability/performance/safety)、Ollama による docs の LLM 評価 (interaction)、Forgejo API の Issue/PR メタデータ (25019 QiU: beneficialness/acceptability) を集計し pg-main の quality DB にスコアを時系列保存する (SonarQube 経路の構成は [docs/runbooks/sonarqube-scan.md](docs/runbooks/sonarqube-scan.md))。イメージは Forgejo コンテナレジストリから配布する (build/push/pull 経路は [docs/runbooks/forgejo-registry.md](docs/runbooks/forgejo-registry.md))。Macaron は Forgejo 非対応のため `[git_service.local_repo]` エスケープハッチで clone 済みリポジトリをローカル解析する (nightly Job の initContainer)。

Phase 5 (観測と改善) 完了: Grafana が quality DB を読み取り専用ロール `quality_ro` の PostgreSQL datasource (uid=`quality-postgres`) で可視化し、スコア時系列ダッシュボードと劣化検知アラート 4 本 (前回 run 比 overall -5 / 特性 -10、絶対閾値 overall<50、30h 鮮度) を `manifests/monitoring-quality/` の ConfigMap で sidecar provisioning する。アラートは Grafana unified alerting が評価し、provisioning した contact point (prometheus-alertmanager 型) + root policy で既存 Alertmanager に送る (datasource の `handleGrafanaManagedAlerts` は org admin config が file provisioning 不可のため不採用)。ダッシュボード/ルールの変更は必ず Git 経由 (運用は [docs/runbooks/quality-observability.md](docs/runbooks/quality-observability.md))。

Phase 6 (通知と復旧訓練) 完了: (1) `am-forgejo-bridge/` (Python 標準ライブラリのみ、自作イメージ) が Alertmanager の receiver `forgejo-issues` (webhook) を受けて Forgejo (sakai/local-infra) に Issue を自動起票する。対象は品質劣化アラート (source=quality-agent) とバックアップ/評価系 namespace の KubeJobFailed。「1 アラートグループ = 1 open Issue」を body 内マーカーで dedup、repeat 通知は吸収、resolve で自動クローズ。route/receiver は `charts/monitoring/values.yaml` の `alertmanager.config` (chart デフォルト config 全体を再掲して拡張する形式)。(2) 層 1 論理ダンプを `manifests/backups/` に実装 (backups Application): forgejo-dump (data PVC を read-only 共有、03:00 JST)・postgres-dump (pg_dumpall、03:15 JST。pg-main は `enableSuperuserAccess: true`)・sealed-secrets 鍵の週次 GPG バックアップ (歴代全鍵、パスフレーズは `backup-gpg-passphrase` SealedSecret + パスワードマネージャ二重管理)。Grafana/ArgoCD の個別ダンプは Git + pg ダンプで再現可能なため廃止。(3) 月次 restore テスト (`backup-restore-test`、毎月 1 日 07:00 JST) が最新ダンプを使い捨て PostgreSQL に実復元して検証し、失敗は Issue 起票につながる。(4) DR 手順は [docs/runbooks/disaster-recovery.md](docs/runbooks/disaster-recovery.md) (復旧順序: sealed-secrets 鍵 → CNPG/pg 復元 → Forgejo → ArgoCD で循環依存を断つ)。全フェーズ完了。以降は運用 + 拡張検討。フェーズ計画は [README.md](README.md) の「構築フェーズ計画」を参照。
