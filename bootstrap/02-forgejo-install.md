# Phase 1 - Step 2: Forgejo の手動インストール

[00-prerequisites.md](00-prerequisites.md) / [01-k3d-cluster.yaml](01-k3d-cluster.yaml) 完了が前提。

## ゴール

- `forgejo` namespace に Forgejo 本体 (SQLite バックエンド) が稼働
- `http://forgejo.local.test/` でブラウザアクセス可
- このリポジトリ (`local-infra`) が Forgejo に push 済み
- ArgoCD が Forgejo の内部 Service URL を Git ソースとして利用可能な状態

ここまで来たら次は [`03-argocd-install.md`](03-argocd-install.md) (未作成)。

## 設計メモ

- **Chart は OCI 配信**: `oci://code.forgejo.org/forgejo-helm/forgejo` を直接参照する (`helm repo add` は不要、index.yaml は空)
- **Chart バージョン**: 17.x 系を想定。chart 9.x 時代にあった同梱 PostgreSQL dependency は削除されているため、Phase 1 では **SQLite** で起動
- **Phase 2 で PostgreSQL に移行**: 外部 PostgreSQL release を別途立て、values.yaml の `gitea.config.database` を書き換える

---

## Step 2-1: namespace 作成

```bash
kubectl create namespace forgejo
```

(`helm install --create-namespace` でも可。冪等性を担保するため事前作成を推奨)

---

## Step 2-2: admin Secret 投入

values.yaml の `gitea.admin.existingSecret: forgejo-admin-secret` から参照される Secret を作成。

**注意: ユーザー名に Forgejo の予約語 (`admin`, `api`, `explore`, `user`, `assets`, `help` 等) は使えない**。本ドキュメントでは `sakai` を例として使う。実際には自分の使いたい名前に置き換えること (参考: [Forgejo の reserved names](https://codeberg.org/forgejo/forgejo/src/branch/forgejo/models/user/user.go))。

```bash
# 管理者ユーザー名・パスワードを生成
ADMIN_USERNAME=sakai
ADMIN_PASSWORD=$(openssl rand -base64 24 | tr -d '/+=' | head -c 24)

# 確認・控え (パスワードマネージャに保管!)
echo "Forgejo admin username: $ADMIN_USERNAME"
echo "Forgejo admin password: $ADMIN_PASSWORD"

# Secret 作成
kubectl create secret generic forgejo-admin-secret \
  --namespace forgejo \
  --from-literal=username="$ADMIN_USERNAME" \
  --from-literal=password="$ADMIN_PASSWORD" \
  --from-literal=email="${ADMIN_USERNAME}@local.test"
```

生成したパスワードは **必ずパスワードマネージャ等に保管**。Phase 2 で sealed-secrets 化するまで一時的に手元管理。

---

## Step 2-3: chart のバージョン確認と helm install

利用可能な chart バージョンを確認:

```bash
helm show chart oci://code.forgejo.org/forgejo-helm/forgejo
# version: 17.1.0
# appVersion: 9.0.3   (本ドキュメントは 2026-05 時点の値)
```

リポジトリルートから install:

```bash
cd ~/local-infra   # このリポジトリのパスに置き換え

helm install forgejo oci://code.forgejo.org/forgejo-helm/forgejo \
  --version 17.1.0 \
  --namespace forgejo \
  --values charts/forgejo/values.yaml \
  --wait \
  --timeout 10m
```

初回は image pull + PVC bind + Forgejo 初期化があるため 3-5 分かかる。

確認:

```bash
kubectl -n forgejo get pods           # forgejo-... が Running になる
kubectl -n forgejo get svc            # forgejo-http が ClusterIP:3000
kubectl -n forgejo get ingress        # forgejo.local.test の Ingress
```

---

## Step 2-4: hosts ファイル設定 (Windows 側)

Windows の hosts ファイルに `forgejo.local.test` を 127.0.0.1 にマップする。

**管理者権限の PowerShell** で:

```powershell
Add-Content -Path C:\Windows\System32\drivers\etc\hosts -Value "`n127.0.0.1 forgejo.local.test"
```

確認:

```powershell
Get-Content C:\Windows\System32\drivers\etc\hosts | Select-String "local.test"
```

`127.0.0.1 forgejo.local.test` が出れば OK。

ブラウザで `http://forgejo.local.test/` を開いて Forgejo のホーム画面が出れば成功。

サインインは:
- ユーザー名: Step 2-2 で設定した `$ADMIN_USERNAME` (例: `sakai`)
- パスワード: Step 2-2 で生成した `$ADMIN_PASSWORD`

---

## Step 2-5: Personal Access Token 作成

CLI から push するため、SSH ではなく **HTTP + PAT** を使う想定。Web UI で:

1. 右上のアバター → Settings → Applications → Manage Access Tokens
2. `Token Name`: `local-infra-bootstrap`
3. `Select scopes`: 最低限 `read:user`, `write:repository`, `write:organization`
4. Generate Token → **表示されたトークンを必ず控える** (二度と表示されない)

---

## Step 2-6: local-infra リポジトリ作成

Web UI で:

1. 右上の `+` → New Repository
2. `Owner`: Step 2-2 で設定した管理者ユーザー (例: `sakai`)
3. `Repository Name`: `local-infra`
4. `Visibility`: Private 推奨
5. `Initialize Repository`: **チェックを外す** (push で初期化したいため)
6. Create Repository

---

## Step 2-7: ローカルから push

WSL 内の local-infra リポジトリで:

```bash
cd ~/local-infra   # 適宜置き換え

# まだ git init していない場合
test -d .git || git init
git add -A
git -c user.email=you@local.test -c user.name=sakai commit -m "Initial bootstrap state"

# git remote 追加 (URL の <username> 部分は Step 2-2 のユーザー名に置き換え)
# Forgejo が事実上の唯一のリモートなので慣習に従って `origin` 名で登録する
git remote add origin http://forgejo.local.test/sakai/local-infra.git

# push (HTTP Basic 認証で PAT を password として渡す)
# 認証プロンプトが出たら Username: sakai, Password: 控えた PAT
git push -u origin main
```

`branch 'main' set up to track 'origin/main'` のような表示が出て、Forgejo Web UI でファイルが見えれば成功。

---

## Step 2-8: ArgoCD が参照する内部 Service URL を確認

ArgoCD は Forgejo の Service を **クラスタ内 DNS 名** で指す。Helm chart のデフォルト Service 名を確認:

```bash
kubectl -n forgejo get svc
# forgejo-http の存在を確認 (TYPE=ClusterIP, PORT=3000/TCP)
```

ArgoCD Application が指すべき URL (パスは Step 2-6 のオーナー名に合わせる):

```
http://forgejo-http.forgejo.svc.cluster.local:3000/sakai/local-infra.git
```

(Service 名が `forgejo-http` でなく `forgejo` だった場合は適宜置き換え。これは [`03-argocd-install.md`](03-argocd-install.md) と [`04-root-app.yaml`](04-root-app.yaml) で使う値)

---

## やり直しが必要な時 (再 install)

values.yaml を書き換えて適用したい場合:

```bash
# release の状態確認
helm list -n forgejo

# 設定だけ変えたい時
helm upgrade forgejo oci://code.forgejo.org/forgejo-helm/forgejo \
  --version 17.1.0 \
  --namespace forgejo \
  --values charts/forgejo/values.yaml

# クリーンに作り直す時 (PVC は残るので注意)
helm uninstall forgejo -n forgejo
kubectl -n forgejo delete pvc --all   # 注意: データ全消去
# その後 Step 2-2 から
```

---

## トラブルシュート

| 症状 | 対処 |
| :--- | :--- |
| `helm install` がタイムアウト | `kubectl -n forgejo get events --sort-by='.lastTimestamp'` でイベント確認。PVC PENDING / Image pull 失敗 / Secret 不在 が大半 |
| `secret "forgejo-admin-secret" not found` | Step 2-2 を先に実行する。Secret 投入後は `kubectl -n forgejo rollout restart deployment/forgejo` で Pod 再生成 |
| ブラウザで `forgejo.local.test` が開けない | (a) hosts に書いたか (b) Traefik が 80 番を持っているか `kubectl -n kube-system get svc traefik` で確認 |
| `502 Bad Gateway` が返る | Forgejo Pod が Ready になる前に Ingress を叩いている。`kubectl -n forgejo logs deploy/forgejo` を確認、数十秒待つ |
| `git push` で `unauthorized` | PAT のスコープ不足。`write:repository` が付いているか再確認 |
| `git push` で `repository does not exist` | Forgejo Web UI でリポジトリを作成しているか / オーナー名と URL のパスが一致しているか確認 |
| `helm install` で `not found` (OCI) | `--version` 指定が間違い。`helm show chart oci://code.forgejo.org/forgejo-helm/forgejo` でバージョン確認 |

---

## 次のステップ

- [`03-argocd-install.md`](03-argocd-install.md) (未作成) — ArgoCD を手動 helm install し、Forgejo の内部 Service URL を Git ソースに指定
- [`04-root-app.yaml`](04-root-app.yaml) (未作成) — App-of-Apps の root Application
