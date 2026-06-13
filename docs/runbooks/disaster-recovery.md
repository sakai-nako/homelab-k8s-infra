# Disaster Recovery runbook (新マシン移行 / クラスタ全損からの完全復旧)

[docs/backup.md](../backup.md) の戦略編に対する実行手順書 (Phase 6)。**半年に 1 回はこの手順の dry-run を推奨**。論理ダンプの機械的な検証は `backup-restore-test` CronJob (毎月 1 日 07:00 JST) が自動実施している。

## 適用シナリオ

| シナリオ | 使う層 | 手順 |
| :--- | :--- | :--- |
| **A. マシン買い替え / WSL 全損** | 層 0 (Git) + 層 1 (論理ダンプ) + 鍵 | 本書のフル手順 |
| **B. 特定アプリの PV 破損・誤削除** | 層 2 (Velero) | [部分復元](#部分復元-層-2-velero) のみ |
| **C. 単一 DB の論理破損** | 層 1 (pg ダンプ) | [PostgreSQL のみ復元](#step-6-postgresql-復元) を該当 DB に絞って実施 |

## 復旧に必要な 4 点セット

1. **NAS バックアップ** — `\\nas\share\local-infra-backups\` (logical/ + keys/ + velero/)
2. **GPG パスフレーズ** — パスワードマネージャ管理。鍵バックアップ (`keys/*.gpg`) の復号に必須。**これを失うと SealedSecret は全滅** (平文 Secret を手動で再投入する別経路になる)
3. **Forgejo 管理者パスワード** — パスワードマネージャ管理
4. **本リポジトリのコピー** — 手元 clone、無ければ NAS の forgejo dump zip 内 (`repos/sakai/local-infra.git`) から取得

## バックアップの所在 (何が・どこに・いつ)

| 対象 | 場所 (NAS `local-infra-backups/`) | 周期 | 生成元 |
| :--- | :--- | :--- | :--- |
| Forgejo 一式 (リポジトリ・Issue・添付・LFS・DB ダンプ) | `logical/forgejo/forgejo-<TS>.zip` | 毎日 03:00 JST | `forgejo-dump` CronJob (forgejo ns) |
| pg-main 全 DB (forgejo/grafana/sonarqube/quality) + ロール | `logical/postgres/pg-main-<TS>.sql.gz` | 毎日 03:15 JST | `postgres-dump` CronJob (postgres ns) |
| sealed-secrets master key (歴代全部) | `keys/sealed-secrets-master-<TS>.gpg` | 毎週日曜 03:45 JST | `sealed-secrets-key-backup` CronJob (sealed-secrets ns) |
| クラスタリソース + PV (Velero 形式) | `velero/velero-<TS>.tar.gz` | 毎日 (Velero 04:00 UTC → export 05:00 UTC) | velero + `velero-s3-nas-export` CronJob |

定義はすべて [manifests/backups/](../../manifests/backups/) と [manifests/velero-export/](../../manifests/velero-export/)。いずれかが失敗すると KubeJobFailed → am-forgejo-bridge が Forgejo Issue を自動起票する。

---

## フル復旧手順 (シナリオ A)

所要目安 2〜3 時間。**順序が重要**: 現在の構成は Forgejo → pg-main (CNPG) → ArgoCD → Forgejo の循環依存があるため、CNPG と sealed-secrets を手動 bootstrap してから Forgejo を復元し、最後に ArgoCD を GitOps の輪に戻す。

### Step 0: 前提環境 (約 30 分)

[bootstrap/00-prerequisites.md](../../bootstrap/00-prerequisites.md) に従う: WSL2 (local-infra ディストリ) / Docker / kubectl / helm / k3d / kubeseal、NAS の SMB マウント (`/mnt/nas`)、insecure-registry 設定。

### Step 1: リポジトリ取得 (約 5 分)

```bash
# 手元 clone があればそれを使う。無ければ NAS の最新 forgejo dump から取り出す:
LATEST=$(ls -t /mnt/nas/local-infra-backups/logical/forgejo/forgejo-*.zip | head -1)
mkdir -p /tmp/fj-dump && cd /tmp/fj-dump
unzip -q "$LATEST" 'repos/*'
git clone /tmp/fj-dump/repos/sakai/local-infra.git ~/local-infra
cd ~/local-infra
```

### Step 2: k3d クラスタ作成 (約 5 分)

```bash
k3d cluster create --config bootstrap/01-k3d-cluster.yaml
kubectl get nodes   # 3 ノード Ready
```

subnet 固定 / hostAliases / registries.yaml はこの config が自動で整える ([docs/runbooks/forgejo-registry.md](forgejo-registry.md))。

### Step 3: sealed-secrets controller + master key 復元 (約 10 分)

**最初にやる**。これ以降に apply する SealedSecret 全部の前提。

```bash
helm repo add sealed-secrets https://bitnami-labs.github.io/sealed-secrets
kubectl create namespace sealed-secrets
helm install sealed-secrets sealed-secrets/sealed-secrets \
  -n sealed-secrets -f charts/sealed-secrets/values.yaml

# NAS から最新の鍵バックアップを復号して投入
LATEST_KEY=$(ls -t /mnt/nas/local-infra-backups/keys/sealed-secrets-master-*.gpg | head -1)
gpg --decrypt "$LATEST_KEY" > /tmp/sealed-secrets-master.yaml   # パスフレーズ入力
kubectl apply -f /tmp/sealed-secrets-master.yaml
shred -u /tmp/sealed-secrets-master.yaml
kubectl -n sealed-secrets rollout restart deploy sealed-secrets-controller
```

> 鍵バックアップには歴代の sealing key が全部入っている (週次 CronJob が label `sealedsecrets.bitnami.com/sealed-secrets-key=active` を一括取得するため)。最新の 1 ファイルだけで全 SealedSecret が復号できる。

### Step 4: CNPG operator + pg-main (約 10 分)

```bash
helm repo add cnpg https://cloudnative-pg.github.io/charts
kubectl create namespace cnpg-system
helm install cnpg-operator cnpg/cloudnative-pg \
  -n cnpg-system -f charts/cnpg-operator/values.yaml

# role パスワードの SealedSecret → pg-main Cluster の順で投入
kubectl create namespace postgres
kubectl apply -f secrets/forgejo-pg.yaml -f secrets/grafana-pg-postgres.yaml \
  -f secrets/sonarqube-pg-postgres.yaml -f secrets/quality-pg-postgres.yaml \
  -f secrets/quality-ro-pg-postgres.yaml
kubectl apply -f manifests/postgres/
kubectl -n postgres wait cluster/pg-main --for=condition=Ready --timeout=300s
```

> managed role が「パスワード無し」で作られた場合 (Secret とのレース) は
> `kubectl -n postgres annotate cluster pg-main local-infra/role-reconcile-nudge=1 --overwrite`
> で reconcile を誘発する ([quality-observability.md](quality-observability.md) の既知の罠)。

### Step 5: PostgreSQL 復元 (約 10 分)

```bash
LATEST_PG=$(ls -t /mnt/nas/local-infra-backups/logical/postgres/pg-main-*.sql.gz | head -1)
# pg-main pod へ流し込む (superuser はローカル peer 接続で OK)
gunzip -c "$LATEST_PG" | kubectl -n postgres exec -i pg-main-1 -c postgres -- \
  psql -U postgres -d postgres -q
# 確認: 4 DB が存在し行が入っている
kubectl -n postgres exec pg-main-1 -c postgres -- psql -U postgres -l
```

ダンプは `--clean --if-exists` 付きなので initdb 直後の素の DB 群に上書きで流せる。「role already exists」系のエラーは想定内 (CNPG managed roles が先に作っている)。

### Step 6: Forgejo 復元 (約 20 分)

```bash
# chart install (DB は Step 5 で復元済みの forgejo DB を指す)
kubectl create namespace forgejo
kubectl apply -f secrets/forgejo-admin.yaml
helm install forgejo oci://code.forgejo.org/forgejo-helm/forgejo \
  -n forgejo -f charts/forgejo/values.yaml
kubectl -n forgejo rollout status deploy/forgejo --timeout=300s

# データ実体 (リポジトリ・LFS・添付) を dump zip から書き戻す。
# ※ forgejo に `restore` サブコマンドは無い。dump の内容物を所定パスへ展開する:
#    zip 内 repos/  → /data/git/repositories/
#    zip 内 data/   → /data/gitea/ (lfs, attachments, avatars 等)
#    gitea-db.sql は不要 (DB は Step 5 で復元済み)
POD=$(kubectl -n forgejo get pod -l app=forgejo -o name | head -1)
cd /tmp/fj-dump && unzip -q "$LATEST" -x 'gitea-db.sql' 'log/*'
kubectl -n forgejo exec "$POD" -- mkdir -p /data/git/repositories
tar cf - -C repos . | kubectl -n forgejo exec -i "$POD" -- tar xf - -C /data/git/repositories
tar cf - -C data . | kubectl -n forgejo exec -i "$POD" -- tar xf - -C /data/gitea

# git hook / 検索インデックスの再生成
kubectl -n forgejo exec "$POD" -- forgejo admin regenerate hooks
kubectl -n forgejo rollout restart deploy/forgejo
```

確認: `http://forgejo.local.test` にログインし、リポジトリ・Issue・PR が見えること。

### Step 7: ArgoCD 投入 → GitOps の輪へ復帰 (約 10 分 + 自動同期 15〜30 分)

```bash
# bootstrap/03-argocd-install.md に従い ArgoCD を手動 install
# (repo credential は復元済み Forgejo の PAT がそのまま生きている:
#  PAT は forgejo DB 内にあり、forgejo-repo SealedSecret の復号値と一致する)
kubectl apply -f secrets/forgejo-repo.yaml
kubectl apply -f bootstrap/04-root-app.yaml
```

ArgoCD が残り全部 (monitoring, seaweedfs, velero, quality-agent, sonarqube, trivy, backups, am-forgejo-bridge, secrets, ...) を順次デプロイする。`kubectl -n argocd get applications` で全部 Synced/Healthy になるまで待つ。

> PAT が失効していて repo connection が張れない場合は
> [sealed-secrets.md の鶏卵問題](sealed-secrets.md#鶏卵問題-argocd-repo-credential-を-sealedsecret-化すると詰む) の手順で新 PAT を `kubectl apply` 直接投入する。

### Step 8: 復旧後チェックリスト (約 15 分)

- [ ] `argocd.local.test` — 全 Application が Synced/Healthy
- [ ] `forgejo.local.test` — リポジトリ・Issue・コンテナレジストリ (packages) が見える
- [ ] `grafana.local.test` — Quality Scores ダッシュボードにスコア時系列が出る (pg 復元の検証を兼ねる)
- [ ] `kubectl -n postgres get cluster pg-main` — Ready、managed roles の passwordStatus に resourceVersion あり
- [ ] `kubectl get sealedsecret -A` — 全部 Synced
- [ ] Windows 側 Ollama 稼働 + `ollama-external` ConfigMap の IP が新環境の LAN IP と一致 ([bootstrap/05](../../bootstrap/05-ollama-windows.md))
- [ ] quality-agent nightly を手動発火して E2E 確認:
      `kubectl -n quality-agent create job --from=cronjob/quality-agent-nightly dr-check`
- [ ] 層 1 CronJob を手動発火して NAS 書き込みを確認:
      `kubectl -n postgres create job --from=cronjob/postgres-dump dr-check-pg`
- [ ] WSL 再起動 → k3d が NAT 環境で自動復帰すること (mirrored 化していないか)

---

## 部分復元 (層 2: Velero)

特定 namespace / PV だけ巻き戻す場合。クラスタが生きていることが前提。

```bash
# バックアップ一覧 (CRD 名衝突に注意: 完全修飾名で叩く)
kubectl get backups.velero.io -n velero
velero backup get

# 例: forgejo namespace のみ復元
velero restore create --from-backup <backup-name> --include-namespaces forgejo
velero restore describe <restore-name>
```

SeaweedFS ごと失われた場合は NAS の `velero/velero-<TS>.tar.gz` を展開し、`aws s3 sync` で SeaweedFS の velero バケットへ書き戻してから上記を実行する (逆方向の手順は [manifests/velero-export/export-cronjob.yaml](../../manifests/velero-export/export-cronjob.yaml) のコメント参照)。

---

## 訓練と検証の運用

| 種別 | 周期 | 実施 |
| :--- | :--- | :--- |
| 論理ダンプの復元検証 (自動) | 毎月 1 日 07:00 JST | `backup-restore-test` CronJob。最新 pg ダンプを使い捨て PostgreSQL に実復元して内容検証 + forgejo zip CRC + 鍵鮮度。失敗時は Forgejo Issue が立つ |
| DR dry-run (手動) | 半年に 1 回 | 本書 Step 1〜8 を別 k3d クラスタ名 (`k3d cluster create dr-test ...`) か予備マシンで通す。少なくとも Step 3 (鍵復号) と Step 5 (pg 復元) は必ず通すこと |
| GPG パスフレーズの実在確認 | DR dry-run と同時 | パスワードマネージャから引いて `gpg --decrypt` が通ること (= 「覚えているつもり」の排除) |

## トラブルシュート

| 症状 | 原因と対処 |
| :--- | :--- |
| SealedSecret が `no key could decrypt` | master key 復元前に apply した / 鍵バックアップが古い。Step 3 をやり直し controller を rollout restart |
| pg restore で role エラー多発 | `--clean --if-exists` ダンプなら想定内。DB 存在と行数で判定する (restore-test と同じ基準) |
| Forgejo 起動後にリポジトリが 500 | hooks 未再生成。`forgejo admin regenerate hooks` → rollout restart |
| ArgoCD repo connection Failed | PAT 失効。[鶏卵問題の打破手順](sealed-secrets.md#鶏卵問題-argocd-repo-credential-を-sealedsecret-化すると詰む) |
| 復旧後に DB 依存 pod が restart バースト | pg-main より先に起動しただけ。pg_isready 待ち initContainer で自然回復する (既知事象) |

## 関連ドキュメント

- [docs/backup.md](../backup.md) — バックアップ戦略 (2 層 + 鍵)
- [docs/runbooks/sealed-secrets.md](sealed-secrets.md) — 鍵のバックアップ/リストア詳細、鶏卵問題
- [docs/runbooks/forgejo-registry.md](forgejo-registry.md) — レジストリ pull 経路の復旧
- [docs/runbooks/quality-observability.md](quality-observability.md) — 品質観測スタックの検証
