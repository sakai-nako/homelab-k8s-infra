# Sealed Secrets 運用ワークフロー

[README](../../README.md) / [docs/backup.md](../backup.md) と合わせて読む。本ドキュメントは Secret を Git で扱うための日常運用手順を扱う。

## 概要

[bitnami-labs/sealed-secrets](https://github.com/bitnami-labs/sealed-secrets) は、Kubernetes Secret を公開鍵暗号で暗号化して **Git にコミット可能** にするコントローラ。クラスタ内に置かれた **Master Key (秘密鍵)** だけが復号できる仕組みなので、SealedSecret マニフェストは公開リポジトリでも安全。

```
平文 Secret  ──── kubeseal (公開鍵で暗号化) ────▶  SealedSecret (暗号文)
                                                            │
                                                            ▼
                                                       Git にコミット OK
                                                            │
                                                            ▼
                                          ArgoCD が apply → クラスタへ
                                                            │
                                                            ▼
                                      controller (秘密鍵で復号) → Secret 作成
```

## なぜ採用したか

- **Git 単一真実 (GitOps) の維持**: Secret を Git 外で手動投入する例外をなくす
- **新マシン移行時の DR**: Git + master key だけでクラスタ状態を完全復元
- **外部 KMS 不要**: 個人ローカル運用にちょうどよい軽量さ

## 重要原則

| 原則 | 内容 |
| :--- | :--- |
| **Master Key を絶対に失わない** | これを失うと既存 SealedSecret 全部が永久に復号不能。NAS に GPG 暗号化してバックアップ ([docs/backup.md](../backup.md#sealed-secrets-鍵の保護)) |
| **クラスタごとに鍵が異なる** | SealedSecret はそのクラスタの master key で暗号化されている。別クラスタに持っていくときは master key も一緒に移すか、再暗号化が必要 |
| **Scope は強い (default: strict)** | SealedSecret は `namespace + name` の組に bind されている。コピペで別 namespace に持っていくと復号失敗。意図的にゆるめたい時は `kubeseal --scope` で変更 |
| **平文 Secret を Git に絶対に置かない** | 暗号化前の YAML は `/tmp` に作って即 shred、もしくは pipe で kubeseal に直接食わせる |

---

## 標準ワークフロー: 新規 Secret を Git 管理に乗せる

### A. ローカルで Secret を作って暗号化する場合

```bash
# 1. 平文 Secret マニフェストを作る (Git の外で、/tmp に)
cat > /tmp/my-secret.yaml <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: my-secret
  namespace: my-namespace
type: Opaque
stringData:
  api-token: "実際のトークン文字列"
EOF

# 2. SealedSecret に変換 (出力は secrets/ ディレクトリへ)
kubeseal --controller-namespace sealed-secrets \
         --controller-name sealed-secrets-controller \
         --format yaml \
         < /tmp/my-secret.yaml \
         > secrets/my-secret.yaml

# 3. 平文を確実に削除
shred -u /tmp/my-secret.yaml

# 4. 中身確認 (encryptedData セクションが長い base64 暗号文になっていれば OK)
head -20 secrets/my-secret.yaml

# 5. commit & push
git add secrets/my-secret.yaml
git commit -m "Add SealedSecret: my-secret"
git push origin main
```

ArgoCD の `secrets` Application が次の sync (30 秒以内) で apply → controller が復号 → 対象 namespace に Secret が生成される。

### B. クラスタ内に既に Secret がある場合 (export して暗号化)

```bash
# 1. 既存 Secret を取得して kubeseal にパイプ
kubectl -n <namespace> get secret <secret-name> -o yaml | \
  kubeseal --controller-namespace sealed-secrets \
           --controller-name sealed-secrets-controller \
           --format yaml \
           > secrets/<secret-name>.yaml

# 2. (必要なら) 既存 Secret を controller の adopt 対象にする
#    → 下記「既存 Secret を adopt する」セクション参照

# 3. commit & push
git add secrets/<secret-name>.yaml
git commit -m "Encrypt <secret-name> as SealedSecret"
git push origin main
```

---

## 特殊ケース: 既存 Secret を adopt する

手動で作成した Secret と同名の SealedSecret を apply すると、controller は安全機構で update を拒否する:

```
failed update: Resource "<secret-name>" already exists and is not managed by SealedSecret
```

これは「sealed-secrets 経由で作っていない Secret を勝手に上書きしないため」の保護機構。明示的に adopt させる必要がある。

### 手順

```bash
# 1. 既存 Secret に annotation を付ける
kubectl -n <namespace> annotate secret <secret-name> \
  sealedsecrets.bitnami.com/managed=true --overwrite

# 2. controller が annotation 変更を検知しないことがあるので、
#    rollout restart で確実に再評価させる
kubectl -n sealed-secrets rollout restart deployment/sealed-secrets-controller

# 3. 30 秒待ってから状態確認
kubectl -n <namespace> get sealedsecret <secret-name>
# → STATUS が True (synced) になれば adopt 完了

# 4. Secret に ownerReferences が SealedSecret から張られているか確認
kubectl -n <namespace> get secret <secret-name> -o yaml | grep -A 5 ownerReferences
```

annotation を付けただけでは controller が再評価しないケースがある (annotation の変更は SealedSecret の reconcile 起点にならないため)。**`rollout restart` をセットで実行する**のが確実。

---

## Master Key のバックアップ / リストア

### バックアップ (定期実施)

詳細手順は [docs/backup.md](../backup.md#sealed-secrets-鍵の保護) 参照。要点だけ:

```bash
kubectl -n sealed-secrets get secret \
  -l sealedsecrets.bitnami.com/sealed-secrets-key=active \
  -o yaml > /tmp/sealed-secrets-master.yaml

gpg --symmetric --cipher-algo AES256 \
    --output /tmp/sealed-secrets-master.gpg \
    /tmp/sealed-secrets-master.yaml

cp /tmp/sealed-secrets-master.gpg \
   /mnt/nas/local-infra-backups/keys/sealed-secrets-master-$(date -u +%Y%m%dT%H%M%S).gpg

shred -u /tmp/sealed-secrets-master.yaml
rm /tmp/sealed-secrets-master.gpg
```

GPG パスフレーズは **パスワードマネージャ管理**。Git にも NAS にも絶対に書かない。

### リストア (新マシン / クラスタ再構築時)

```bash
# 1. NAS から最新の暗号化バックアップを取得
ls -t /mnt/nas/local-infra-backups/keys/sealed-secrets-master-*.gpg | head -1

# 2. GPG 復号 (パスフレーズプロンプト)
gpg --decrypt \
    /mnt/nas/local-infra-backups/keys/sealed-secrets-master-<latest>.gpg \
    > /tmp/sealed-secrets-master.yaml

# 3. sealed-secrets controller が動いている前提で、master key を投入
kubectl apply -f /tmp/sealed-secrets-master.yaml

# 4. 平文を削除
shred -u /tmp/sealed-secrets-master.yaml

# 5. controller を rollout restart して新 key を読ませる
kubectl -n sealed-secrets rollout restart deployment/sealed-secrets-controller

# 6. 動作確認: 既存の SealedSecret が復号できているか
kubectl get sealedsecret -A
# → すべての STATUS が True なら復号成功
```

---

## 鶏卵問題: ArgoCD repo credential を SealedSecret 化すると詰む

ArgoCD が Forgejo へ HTTP basic auth でアクセスするための PAT を SealedSecret で管理している構成では、**PAT が無効化された瞬間に GitOps 経由での credential 更新が不可能になる** 鶏卵 (chicken-and-egg) 状態が発生する。

```
ArgoCD は古い PAT で repo にアクセスしようとする
     ↓ 認証失敗
ArgoCD は Forgejo から最新の SealedSecret を取得できない
     ↓
secrets Application は古い SealedSecret マニフェストのまま
     ↓
sealed-secrets controller は古い暗号文を復号して古い PAT を Secret に書く
     ↓
ArgoCD は再び古い PAT で... (無限ループ)
```

### 打破策: ArgoCD を bypass して `kubectl apply` で SealedSecret を直接投入

新 PAT で SealedSecret を生成 → **GitOps を経由せず** にクラスタへ直接 apply する。

```bash
# 1. 新 PAT で SealedSecret manifest を再生成 (kubeseal の通常フロー)
NEW_PAT='<Forgejo Web で再発行した PAT>'
cat <<EOF | kubeseal --controller-namespace sealed-secrets \
                     --controller-name sealed-secrets-controller \
                     --format yaml \
                     > secrets/forgejo-repo.yaml
apiVersion: v1
kind: Secret
metadata:
  name: forgejo-repo
  namespace: argocd
  labels:
    argocd.argoproj.io/secret-type: repository
type: Opaque
stringData:
  type: git
  url: http://forgejo-http.forgejo.svc.cluster.local:3000/<owner>/local-infra.git
  username: <owner>
  password: $NEW_PAT
EOF

# 2. ★ ArgoCD を bypass して直接 apply
kubectl apply -f secrets/forgejo-repo.yaml

# 3. 既存 Secret を消して controller に新版から再生成させる
kubectl -n argocd delete secret forgejo-repo
sleep 5

# 4. 復号結果が新 PAT になっているか確認
kubectl -n argocd get secret forgejo-repo -o jsonpath='{.data.password}' | base64 -d ; echo

# 5. Git にも push (これで通常運用に戻る)
git add secrets/forgejo-repo.yaml
git commit -m "Refresh forgejo-repo PAT"
git push origin main
```

ArgoCD repo connection が回復したら、以降は GitOps だけで動く正常状態に戻る。

### 教訓

| 種類 | 例 | 対応 |
| :--- | :--- | :--- |
| **bootstrap-critical secret**: ArgoCD 自身が動くために必要な credential | `forgejo-repo`, `argocd-server cert`, `sealed-secrets-key` | 鶏卵問題を想定。手動の `kubectl apply` ルートを runbook 化 |
| 通常の application secret | DB password, API key 等 | 普通の GitOps フローで OK |

特にこのプロジェクトでは、**Forgejo の PostgreSQL 移行で DB がリセットされると全 PAT が一掃される**ので、移行作業前に「移行後に新 PAT を発行して `kubectl apply` で投入する」段取りを事前に決めておくこと。

### NEW_PAT 変数のチェックリスト (経験則)

PAT を変数経由で kubeseal に渡す時の罠を回避するチェックリスト:

```bash
# 1. シェル変数が想定値かを必ず先頭確認
echo "Length: ${#NEW_PAT}"          # → 40 が出ること (Forgejo PAT のデフォルト)
echo "Prefix: ${NEW_PAT:0:8}"       # → コピペした PAT の先頭と一致すること

# 2. heredoc に流す前に、一度中間ファイルに書いて目視確認
cat > /tmp/secret.yaml <<EOF
...
  password: $NEW_PAT
EOF
grep password /tmp/secret.yaml      # → 想定値が見えること

# 3. kubeseal は中間ファイルから読む (heredoc のシェル展開が信用できない場合)
kubeseal --controller-namespace sealed-secrets \
         --controller-name sealed-secrets-controller \
         --format yaml \
         < /tmp/secret.yaml \
         > secrets/forgejo-repo.yaml

# 4. 平文中間ファイルは shred
shred -u /tmp/secret.yaml

# 5. 結果の暗号文が以前と違うことを diff で確認
diff <(git show HEAD:secrets/forgejo-repo.yaml) secrets/forgejo-repo.yaml | head
```

---

## トラブルシュート

| 症状 | 原因 | 対処 |
| :--- | :--- | :--- |
| `failed update: ... already exists and is not managed by SealedSecret` | 同名の Secret が手動作成されていて adopt 設定が無い | [既存 Secret を adopt する](#特殊ケース-既存-secret-を-adopt-する) の手順を実行 |
| annotation を付けたのに adopt されない | annotation 変更が controller の reconcile 起点にならない | `kubectl -n sealed-secrets rollout restart deployment/sealed-secrets-controller` |
| `Error: cannot fetch certificate` (kubeseal 実行時) | controller への接続失敗 | `--controller-namespace`/`--controller-name` の指定が正しいか確認。本プロジェクトは `sealed-secrets` ns / `sealed-secrets-controller` で固定 |
| `no key could decrypt this secret` (apply 時) | SealedSecret がこのクラスタの master key で暗号化されていない (別クラスタ用 or 鍵が紛失) | 元クラスタの master key をリストア、もしくは平文から再暗号化 |
| SealedSecret が一見正常だが Secret が生成されない | namespace が無い / scope ミスマッチ | controller ログ確認 `kubectl -n sealed-secrets logs deploy/sealed-secrets-controller --tail 50` |
| ArgoCD repo connection Failed、SealedSecret push しても解消しない | [鶏卵問題](#鶏卵問題-argocd-repo-credential-を-sealedsecret-化すると詰む) | `kubectl apply -f secrets/forgejo-repo.yaml` で ArgoCD bypass |
| `kubectl patch secret` で password 直接更新したのに数十秒後に元に戻る | sealed-secrets controller の reconcile が SealedSecret CR の暗号文で Secret を上書き | patch ではなく **SealedSecret CR の更新** で対応する |

---

## 関連ドキュメント

- [docs/backup.md](../backup.md) — Master key の GPG バックアップ手順、層 1 バックアップ全体像
- [docs/architecture.md](../architecture.md#secret-管理方針) — Secret 管理戦略の設計判断
- [charts/sealed-secrets/values.yaml](../../charts/sealed-secrets/values.yaml) — controller の Helm values
- [clusters/home/apps/secrets.yaml](../../clusters/home/apps/secrets.yaml) — SealedSecret 群を束ねる ArgoCD Application
