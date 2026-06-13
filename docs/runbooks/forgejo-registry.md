# Forgejo コンテナレジストリ運用 (build / push / クラスタ pull)

自作イメージ (quality-agent 等) を Forgejo 内蔵のコンテナレジストリで配布し、k3d クラスタから pull させるための手順と仕組み。Phase 4 の quality-agent CronJob (`manifests/quality-agent/cronjob.yaml`) で確立したパターン。

## 全体像

Forgejo は Traefik ingress で `http://forgejo.local.test` (TLS なし) に公開されており、レジストリ API は同一ホストの `/v2/` 配下にある。経路は 2 つ:

| 経路 | 解決方法 |
| :--- | :--- |
| **WSL → push** | `forgejo.local.test` は Windows hosts の `127.0.0.1` に解決 → k3d serverlb の published port 80 → Traefik。HTTP のため docker daemon に insecure-registry 設定が必要 ([bootstrap/00 Step 5-b](../../bootstrap/00-prerequisites.md)) |
| **k3d ノード → pull** | ノードコンテナ内で `127.0.0.1` 解決では自分自身を指して死ぬ。ノードの `/etc/hosts` で `forgejo.local.test → 172.18.0.1` (docker network GW = WSL ホスト) に向け、`registries.yaml` の HTTP mirror で containerd に plain-HTTP を許可する |

宣言の所在は [bootstrap/01-k3d-cluster.yaml](../../bootstrap/01-k3d-cluster.yaml) の `subnet` / `hostAliases` / `registries`。**subnet を `172.18.0.0/16` に固定**しているのは、GW アドレス (`172.18.0.1`) を hostAliases に直書きしても クラスタ再作成で変わらないようにするため。クラスタを `k3d cluster create --config` で作り直せば pull 経路は自動で整う。

パッケージは public 公開 (user `sakai` は public + `REQUIRE_SIGNIN_VIEW: false`) のため **pull は匿名で可能**。imagePullSecret は不要。

## イメージの build / push

```bash
# WSL (local-infra ディストリ) 内で
docker build -t forgejo.local.test/sakai/quality-agent:<version> quality-agent/
docker login forgejo.local.test -u sakai   # PAT は write:package scope (下記)
docker push forgejo.local.test/sakai/quality-agent:<version>
```

タグはバージョンを上げて push し、`manifests/quality-agent/cronjob.yaml` の image タグを Git で更新して ArgoCD に sync させる。**同一タグへの上書き push は反映されない** (`imagePullPolicy: IfNotPresent` のため)。

## PAT の scope に注意

- git push 用の PAT (`write:repository`) では **docker login は通るのに push が 401 になる**。レジストリ push には `write:package` scope が必要。
- 生成は Forgejo UI (設定 → アプリケーション) か、admin CLI:

```bash
kubectl -n forgejo exec deploy/forgejo -c forgejo -- \
  forgejo admin user generate-access-token \
  --username sakai --token-name docker-package-push --scopes write:package --raw
```

- 生成済みトークンは UI からいつでも revoke できる。docker login 後は WSL の `~/.docker/config.json` に保存されるため、push のたびに login し直す必要はない。

## 稼働中クラスタへの in-place 適用 (再作成しない場合)

bootstrap 設定はクラスタ作成時にしか効かないため、稼働中クラスタには手動で同じ状態を作る (2026-06-11 に適用済):

```bash
# 各ノード (server-0 / agent-0 / agent-1) に対して
docker exec <node> sh -c 'echo "172.18.0.1 forgejo.local.test" >> /etc/hosts'
docker exec <node> mkdir -p /etc/rancher/k3s
docker cp registries.yaml <node>:/etc/rancher/k3s/registries.yaml   # 内容は bootstrap/01 の registries.config と同一
docker restart <node>   # k3s は起動時にしか registries.yaml を読まない。1 台ずつ Ready を待って順次
```

`/etc/hosts` への追記は docker restart では消えないが、**コンテナ再作成 (= クラスタ再作成) では消える**。その場合は bootstrap 設定が代わりに効くので問題ない。

適用確認:

```bash
docker exec k3d-home-agent-0 cat /var/lib/rancher/k3s/agent/etc/containerd/certs.d/forgejo.local.test/hosts.toml
# [host."http://forgejo.local.test/v2"] / capabilities = ["pull", "resolve"] が出れば OK
```

## 匿名 pull の動作確認

```bash
TOKEN=$(curl -s "http://forgejo.local.test/v2/token?scope=repository:sakai/quality-agent:pull" | jq -r .token)
curl -s -o /dev/null -w '%{http_code}\n' \
  -H "Authorization: Bearer $TOKEN" \
  -H "Accept: application/vnd.oci.image.index.v1+json" \
  http://forgejo.local.test/v2/sakai/quality-agent/manifests/<version>
# 200 なら匿名 pull 可能
```

## トラブルシュート

| 症状 | 原因と対処 |
| :--- | :--- |
| `docker login` が 404 | Traefik / Forgejo が未復帰 (クラスタ再起動直後に多い)。`kubectl -n forgejo get pods` で Ready を待つ |
| login は成功するのに push が 401 | PAT の scope 不足。`write:package` の PAT を作り直す (上記) |
| pod が `ImagePullBackOff` | ノードに hosts エントリ / registries.yaml が無い (新ノード追加・クラスタ再作成直後に bootstrap 設定を使わなかった等)。in-place 適用手順を実施 |
| イメージを更新したのに反映されない | 同一タグ上書きは pull されない。バージョンタグを上げて cronjob.yaml も更新 |
| Forgejo init コンテナが CrashLoopBackOff | pg-main 復帰前に起動しただけ。リトライで自然回復する |
