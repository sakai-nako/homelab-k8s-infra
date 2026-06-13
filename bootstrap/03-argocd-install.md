# Phase 1 - Step 3: ArgoCD の手動インストール

[02-forgejo-install.md](02-forgejo-install.md) 完了が前提。Forgejo に本リポジトリが push 済であること。

## ゴール

- `argocd` namespace に ArgoCD が稼働
- `http://argocd.local.test/` でブラウザアクセス可
- ArgoCD が Forgejo の内部 Service (`forgejo-http.forgejo.svc.cluster.local:3000`) を Git ソースとして認識
- このリポジトリの `clusters/home/` 配下の root Application が Apply 可能な状態

ここまで来たら次は [`04-root-app.yaml`](04-root-app.yaml) (未作成)。

## 設計メモ

- **Chart 配信**: `argo/argo-cd` (`https://argoproj.github.io/argo-helm`)、helm repo add で取得
- **TLS**: ArgoCD 自身は HTTP モード (`server.insecure: true`)。Traefik 経由で `argocd.local.test:80` に届ける
- **Repo 認証**: Forgejo がプライベートリポジトリなので、PAT 入りの **repo Secret** を ArgoCD に登録する必要がある
- **PAT 再利用**: Step 2-5 で作った PAT に `read:repository` scope があれば再利用可能。無ければ scope `read:repository` 付きで新たに作成

---

## Step 3-1: Helm リポジトリ追加

```bash
helm repo add argo https://argoproj.github.io/argo-helm
helm repo update
helm search repo argo/argo-cd | head
```

`argo/argo-cd` の最新版が表示されれば OK (本ドキュメントは Chart 7.x 系を想定)。

---

## Step 3-2: namespace 作成

```bash
kubectl create namespace argocd
```

---

## Step 3-3: Forgejo の repo Secret を投入

ArgoCD が Forgejo の Private リポジトリを clone できるよう、PAT 入りの Secret を `argocd` namespace に作成。

```bash
# Step 2-5 で作った PAT (環境変数として export しておく)
FORGEJO_PAT=<Step 2-5 で控えた PAT を貼る>
FORGEJO_USER=sakai   # Step 2-2 で設定したユーザー名

kubectl apply -n argocd -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: forgejo-repo
  namespace: argocd
  labels:
    # この label が ArgoCD に「これは repo credential だよ」と教える
    argocd.argoproj.io/secret-type: repository
type: Opaque
stringData:
  type: git
  url: http://forgejo-http.forgejo.svc.cluster.local:3000/${FORGEJO_USER}/local-infra.git
  username: ${FORGEJO_USER}
  password: ${FORGEJO_PAT}
EOF
```

PAT を平文で `kubectl apply` するのは Phase 1 限定の妥協。Phase 2 で sealed-secrets 化する。

---

## Step 3-4: helm install

```bash
cd ~/local-infra   # このリポジトリのパスに置き換え

helm install argocd argo/argo-cd \
  --namespace argocd \
  --values charts/argocd/values.yaml \
  --wait \
  --timeout 10m
```

初回は数分かかる。`kubectl -n argocd get pods` で全 Pod が Running になるのを待つ。

---

## Step 3-5: hosts ファイル設定 (Windows 側)

**管理者権限の PowerShell** で:

```powershell
Add-Content -Path C:\Windows\System32\drivers\etc\hosts -Value "`n127.0.0.1 argocd.local.test"
ipconfig /flushdns
```

ブラウザで `http://argocd.local.test/` を開いて ArgoCD のログイン画面が出れば成功。

---

## Step 3-6: 初期 admin パスワードを取得

ArgoCD は初回起動時にランダムな admin パスワードを生成し Secret に保存する。

```bash
kubectl -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath='{.data.password}' | base64 -d ; echo
```

出力された文字列が初期パスワード。**必ずパスワードマネージャに保管**。

UI ログイン:
- Username: `admin`
- Password: 取得した文字列

ログイン後、右上アカウントメニュー → Update Password で **任意のパスワードに変更推奨**。変更後、初期 Secret は削除して良い:

```bash
kubectl -n argocd delete secret argocd-initial-admin-secret
```

---

## Step 3-7: Forgejo repo が認識されているか確認

ArgoCD UI で:

1. 左メニュー → Settings → Repositories
2. `http://forgejo-http.forgejo.svc.cluster.local:3000/sakai/local-infra.git` が一覧に出ているか
3. Connection Status が `Successful` (緑チェック) になっているか

CLI で確認したい場合:

```bash
# port-forward 経由でログイン
kubectl -n argocd port-forward svc/argocd-server 8080:80 &
argocd login localhost:8080 --username admin --password '<取得した初期パスワード>' --insecure
argocd repo list
```

(argocd CLI が無ければ `https://github.com/argoproj/argo-cd/releases` から install してから)

---

## トラブルシュート

| 症状 | 対処 |
| :--- | :--- |
| `helm install` がタイムアウト | `kubectl -n argocd get events --sort-by='.lastTimestamp'` を確認。多くは Pod の image pull に時間がかかっているだけ。十分待つ |
| ブラウザで argocd.local.test が開けない | (a) hosts 設定 (b) `ipconfig /flushdns` (c) ブラウザの DNS キャッシュクリア — Step 2-4 と同じパターン |
| `argocd-server` が `502 Bad Gateway` | `server.insecure: true` が values に効いているか確認。`kubectl -n argocd get cm argocd-cmd-params-cm -o yaml` |
| Repo connection が `Failed` | (a) PAT の scope に `read:repository` があるか (b) URL のオーナー部分が Forgejo のユーザー名と一致しているか (c) Forgejo Pod が Running か |
| `kubectl get secret argocd-initial-admin-secret` で not found | Pod 起動が完了していない。`kubectl -n argocd get pods` で `argocd-server` が Ready か確認 |

---

## 次のステップ

- [`04-root-app.yaml`](04-root-app.yaml) (未作成) — App-of-Apps の root Application を kubectl apply
- その後は ArgoCD UI から sync 操作 → 各 Application が順次デプロイされる流れに移行
