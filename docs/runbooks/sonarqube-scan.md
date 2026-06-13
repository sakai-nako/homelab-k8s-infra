# SonarQube nightly 解析と quality-agent 連携の有効化

quality-agent の maintainability 特性 (技術的負債比・重複行密度) は SonarQube の解析結果から取る。経路は 2 段の nightly CronJob (どちらも `quality-agent` namespace):

| 時刻 (JST) | CronJob | 役割 |
| :--- | :--- | :--- |
| 05:15 | `sonar-scan-nightly` | local-infra を Forgejo から clone し sonar-scanner で解析 → SonarQube に結果を蓄積 |
| 06:00 | `quality-agent-nightly` | `collect --sources trivy,sonarqube` で Web API から保守性メジャーを取得しスコア化 → quality DB へ保存 |

解析設定はリポジトリ直下の [sonar-project.properties](../../sonar-project.properties) (プロジェクトキー `local-infra`)。

## トークン未投入時の挙動 (GitOps eventual 収束)

必要な Secret は 2 つで、CronJob 側は **どちらも `optional: true`** で参照する。SealedSecret 投入前は:

- `sonar-scan-nightly` … スクリプトが env 空を検知してメッセージを出し **exit 0** (Job は成功扱い)
- `quality-agent-nightly` … sonarqube collector が warning を出して **その source だけスキップ**。security スコアは通常どおり保存され、score の notes に maintainability 除外が記録される

つまり下記の有効化手順を踏むまでは「security のみの nightly」が壊れずに動き続け、SealedSecret を commit した翌晩から自動で maintainability が乗る。

## 有効化手順 (1 回だけ)

### 1. SonarQube ユーザートークンの生成

http://sonar.local.test に admin でログイン (パスワードは Phase 3 セットアップ時に変更したもの。パスワードマネージャ参照):

**My Account → Security → Generate Tokens** で Type: **User Token**、有効期限なしで生成し `squ_...` を控える。

- User Token にするのは、解析の投入 (sonar-scanner) と Web API の読み出し (quality-agent) を 1 本で賄うため。admin のトークンなら初回スキャン時のプロジェクト自動作成権限もある。

### 2. Forgejo 読み取り PAT の生成

リポジトリが private のため `sonar-scan-nightly` の clone に必要。Forgejo UI (http://forgejo.local.test) → 設定 → アプリケーション → トークンの生成で scope **read:repository** のみの PAT を作る。

### 3. SealedSecret 化して commit (WSL 内)

平文を残さないため `read -rs` で受けて pipe で kubeseal に流す ([sealed-secrets runbook](sealed-secrets.md) と同方針):

```bash
cd /mnt/c/Users/sakai/Main/repos/local-infra

read -rs SONAR_TOKEN    # squ_... を貼り付けて Enter
kubectl -n quality-agent create secret generic sonarqube-token \
  --from-literal=token="$SONAR_TOKEN" --dry-run=client -o yaml \
| kubeseal --controller-namespace sealed-secrets \
           --controller-name sealed-secrets-controller \
           --format yaml > secrets/sonarqube-token.yaml

read -rs FORGEJO_PAT    # read:repository,read:issue の PAT を貼り付けて Enter
                        # (read:issue は forgejo collector の Issue/PR API 読み取りに必要)
kubectl -n quality-agent create secret generic forgejo-read \
  --from-literal=pat="$FORGEJO_PAT" --dry-run=client -o yaml \
| kubeseal --controller-namespace sealed-secrets \
           --controller-name sealed-secrets-controller \
           --format yaml > secrets/forgejo-read.yaml

unset SONAR_TOKEN FORGEJO_PAT
git add secrets/sonarqube-token.yaml secrets/forgejo-read.yaml
git commit -m "Phase 4: SonarQube 解析用トークンを SealedSecret 投入"
git push origin main
```

`secrets` Application が自動 sync して `quality-agent` namespace に Secret が落ちる。

### 4. 動作確認 (翌晩を待たない場合)

```bash
# 解析を手動実行 (初回は SonarQube 側にプロジェクト local-infra が自動作成される)
kubectl -n quality-agent create job --from=cronjob/sonar-scan-nightly sonar-scan-manual
kubectl -n quality-agent logs -f job/sonar-scan-manual   # EXECUTION SUCCESS を確認

# 評価を手動実行
kubectl -n quality-agent create job --from=cronjob/quality-agent-nightly quality-agent-manual
kubectl -n quality-agent logs -f job/quality-agent-manual
# レポートに maintainability 行が出て、warning が消えていれば OK

# 後片付け
kubectl -n quality-agent delete job sonar-scan-manual quality-agent-manual
```

SonarQube UI (http://sonar.local.test) の Projects に `local-infra` が現れ、quality DB の `quality_characteristic_scores` に `maintainability` 行が増えていることも確認できる。

## トラブルシュート

| 症状 | 原因と対処 |
| :--- | :--- |
| scan ログが「FORGEJO_PAT 未投入 ... スキップ」 | SealedSecret `forgejo-read` 未投入 or 鍵名違い (key は `pat`)。手順 3 を実施 |
| scan ログが「SONAR_TOKEN 未投入 ... スキップ」 | SealedSecret `sonarqube-token` 未投入 or 鍵名違い (key は `token`) |
| collect が「認証に失敗しました (HTTP 401/403)」 | トークン失効・revoke 済み。手順 1 で再生成して SealedSecret を作り直す |
| collect が「プロジェクト 'local-infra' が見つかりません」 | sonar-scanner がまだ一度も成功していない。`sonar-scan-nightly` のログを確認 |
| scanner が OOMKilled | 解析対象の肥大。`sonar-scan-cronjob.yaml` の memory limit (2Gi) を引き上げる |
| clone が 401 | PAT 失効。Forgejo で再生成し `forgejo-read` を作り直す |
