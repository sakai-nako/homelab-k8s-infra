# 品質スコア観測スタック運用 (Grafana ダッシュボード / 劣化検知アラート)

quality-agent が quality DB に保存するスコア時系列を Grafana で可視化し、劣化を Grafana unified alerting で検知して Alertmanager に転送する仕組み (Phase 5) の運用手順。

## データフロー全体像

```
quality-agent nightly CronJob (06:00 JST)
   └→ pg-main の quality DB に時系列 INSERT (quality_runs / quality_characteristic_scores)
        └→ Grafana datasource "Quality (PostgreSQL)" (uid=quality-postgres, ロール quality_ro で SELECT)
             ├→ ダッシュボード "Quality Scores (ISO 25010/25019)" (uid=quality-scores)
             └→ Grafana-managed アラートルール 4 本 (10 分間隔で SQL 評価)
                  └→ contact point (prometheus-alertmanager 型) + root policy で Alertmanager へ送信
                       └→ receiver `forgejo-issues` → am-forgejo-bridge (monitoring ns) が
                          Forgejo (sakai/local-infra) に Issue を自動起票、resolve で自動クローズ (Phase 6)
```

> **転送方式の補足**: datasource の `handleGrafanaManagedAlerts` による転送は採用していない。org の ngalert admin config レコードが無いと external AM discovery が走らず (実測で `/api/v1/ngalert/alertmanagers` が空)、admin config は file provisioning できないため GitOps 化できない。contact point + notification policy は alerting file provisioning で宣言管理できる。

| 構成要素 | 所在 (Git が真実) |
| :--- | :--- |
| datasource / alerts sidecar | `charts/monitoring/values.yaml` (grafana ブロック) |
| ダッシュボード JSON | `manifests/monitoring-quality/dashboard-quality-scores.yaml` |
| アラートルール / AM への contact point + policy | `manifests/monitoring-quality/alert-rules.yaml` |
| AM の route / receiver (Issue 起票先) | `charts/monitoring/values.yaml` (alertmanager.config) |
| Issue 起票 bridge 本体 | `am-forgejo-bridge/` (実装) + `manifests/am-forgejo-bridge/` (Deployment/Service) + `secrets/am-forgejo-bridge-token.yaml` (write:issue PAT) |
| quality_ro ロール | `manifests/postgres/cluster.yaml` (managed.roles) |
| quality_ro パスワード | `secrets/quality-ro-pg-postgres.yaml` (postgres ns) + `secrets/quality-ro-pg-monitoring.yaml` (monitoring ns) |
| SELECT GRANT | `quality-agent/quality_agent/report/schema.sql` の DO ブロック (nightly が冪等適用) |

## アラートルール一覧

| uid | 条件 | severity | noDataState |
| :--- | :--- | :--- | :--- |
| quality-overall-drop | overall が前回 run 比 5.0 以上低下 | warning | OK |
| quality-characteristic-drop | いずれかの特性が前回 run 比 10.0 以上低下 (両 run とも insufficient_data=false の特性のみ) | warning | OK |
| quality-overall-low | overall < 50 | critical | OK |
| quality-run-stale | 最終 run から 30 時間超 (run ゼロ件でも発火) | warning | Alerting |

閾値は `gt 4.9` ≡ `>= 5.0` のように 0.1 刻み (NUMERIC(5,1)) を前提に表現している。スキーマの精度を変えたら evaluator も見直すこと。

## ダッシュボードの変更フロー

provisioning 由来のダッシュボードは UI から直接保存できない。**「Save as」で DB コピーを作らない**こと (ConfigMap と drift して二重管理になる)。

1. `grafana.local.test` でダッシュボードを開き、パネル編集で見た目や SQL を調整 (保存はできないが編集・プレビューは可能)
2. Dashboard settings → JSON Model (または Export → Save JSON) で JSON をコピー
3. `manifests/monitoring-quality/dashboard-quality-scores.yaml` の `quality-scores.json` を置き換える。`uid: quality-scores` と datasource の `uid: quality-postgres` が残っていることを確認
4. `git push` → ArgoCD sync → dashboard sidecar が約 1 分以内に再取り込み

## アラート閾値の調整

provisioning 由来のルールは UI で **Provisioned バッジが付き編集不可**。必ず `manifests/monitoring-quality/alert-rules.yaml` を編集して `git push` する。sidecar (`grafana-sc-alerts`) が ConfigMap の変更を検出して reload API を叩く。

反映確認:

```bash
# (WSL 内) provenance=file で 4 本見えれば OK
curl -s -u "<admin>:<pass>" http://grafana.local.test/api/v1/provisioning/alert-rules \
  | jq -r '.[] | select(.uid | startswith("quality-")) | "\(.uid): \(.title)"'
```

## quality_ro パスワードのローテーション

1. `docs/runbooks/sealed-secrets.md` の手順で新パスワードの SealedSecret を 2 枚とも再生成する (postgres ns は basic-auth で `username: quality_ro` 必須、monitoring ns は Opaque で password のみ)。**両方同じパスワード**にすること
2. `git push` → secrets App が sync
3. CNPG が Secret 更新を検知して role の password を ALTER する。反映確認:
   ```bash
   kubectl -n postgres get cluster pg-main \
     -o jsonpath='{.status.managedRolesStatus.passwordStatus.quality_ro}'
   # resourceVersion が新しい Secret のものに変われば反映済み
   ```
4. **Grafana は env 注入なので自動では追従しない**。rollout restart が必要:
   ```bash
   kubectl -n monitoring rollout restart deploy monitoring-grafana
   ```
5. datasource health で確認:
   `grafana.local.test` → Connections → Data sources → Quality (PostgreSQL) → Test

### 罠: ロール作成と Secret 投入のレース (初回構築・全損復旧時)

managed role とその passwordSecret を同じ push で投入すると、**ロールが Secret より先に reconcile されてパスワード無しで作られる**ことがある (`User "quality_ro" has no password assigned` で認証失敗)。CNPG は Secret の後着だけでは再同期しないため、Cluster にイベントを起こして role reconcile を誘発する:

```bash
# passwordStatus に resourceVersion が無い = 未反映のサイン
kubectl -n postgres get cluster pg-main \
  -o jsonpath='{.status.managedRolesStatus.passwordStatus.quality_ro}'

# annotation 付与で reconcile を誘発 (値は何でもよい。Git とは衝突しない)
kubectl -n postgres annotate cluster pg-main local-infra/role-reconcile-nudge=1 --overwrite
```

## テスト発火 (E2E 検証)

劣化 run を注入して検知経路全体を検証する。**検証後は必ず DELETE で掃除する** (時系列を汚すと前回比ルールが次の nightly で誤発火する)。

```bash
# (WSL 内) 1. 劣化 run を注入: 最新 run をコピーして overall=30.0、security を -15
kubectl -n postgres exec -i pg-main-1 -c postgres -- psql -U postgres -d quality <<'SQL'
WITH latest AS (
  SELECT run_id FROM quality_runs
  WHERE target = 'local-infra' ORDER BY scored_at DESC LIMIT 1
), new_run AS (
  INSERT INTO quality_runs (scored_at, target, context_id, overall)
  VALUES (now(), 'local-infra', 'home-monitoring-app', 30.0)
  RETURNING run_id
)
INSERT INTO quality_characteristic_scores (run_id, characteristic, score, weight, insufficient_data)
SELECT n.run_id, s.characteristic,
       CASE WHEN s.characteristic = 'security'
            THEN GREATEST(s.score - 15.0, 0.0) ELSE s.score END,
       s.weight, s.insufficient_data
FROM quality_characteristic_scores s, new_run n, latest l
WHERE s.run_id = l.run_id;
SELECT 'TEST_RUN_ID=' || max(run_id) FROM quality_runs;
SQL

# 2. 最大 10 分 (評価間隔) 待つと quality-overall-drop / quality-overall-low /
#    quality-characteristic-drop (security) が Firing になる
#    grafana.local.test → Alerting → Alert rules で確認

# 3. Alertmanager 到達確認: alertmanager.local.test の UI、または
curl -s http://alertmanager.local.test/api/v2/alerts | jq -r '.[].labels.alertname'

# 4. Issue 起票確認 (group_wait 1m + bridge 処理後):
#    forgejo.local.test/sakai/local-infra/issues に [alert] alertname=... の
#    open Issue が立つ。bridge のログでも確認できる:
kubectl -n monitoring logs deploy/am-forgejo-bridge --tail 20

# 5. 掃除 (TEST_RUN_ID は手順 1 の出力値。FK CASCADE で特性行も消える)
kubectl -n postgres exec pg-main-1 -c postgres -- \
  psql -U postgres -d quality -c "DELETE FROM quality_runs WHERE run_id = <TEST_RUN_ID>"

# 6. 次の評価 (≤10 分) で全ルールが Normal に戻り、AM resolve →
#    bridge が Issue にコメントを付けて自動クローズすることを確認
```

## トラブルシュート

| 症状 | 原因と対処 |
| :--- | :--- |
| datasource health が password authentication failed | (a) quality_ro の password 未反映 → 上記「ロール作成と Secret 投入のレース」。(b) monitoring ns の quality-ro-pg Secret が古い → secrets App の sync 状態と Grafana rollout restart |
| datasource health が `$__env{...}` のまま接続失敗 | QUALITY_PG_PASSWORD env が pod に無い (values の envValueFrom 欠落 / Secret 未同期)。`kubectl -n monitoring get deploy monitoring-grafana -o yaml \| grep QUALITY` で確認 |
| ダッシュボードが出ない | ConfigMap の label `grafana_dashboard: "1"` と monitoring-quality App の sync を確認。dashboard sidecar は全 ns 監視 (NAMESPACE=ALL) |
| アラートルールが出ない | ConfigMap の label `grafana_alert: "1"` と **namespace=monitoring** (alerts sidecar は自 ns のみ監視) を確認。`kubectl -n monitoring logs deploy/monitoring-grafana -c grafana-sc-alerts` でエラーを見る |
| ルールはあるが評価が Error | datasource uid (quality-postgres) の参照切れ、または SQL エラー。Grafana UI の Alerting → ルール詳細 → Query inspector |
| Firing なのに Alertmanager に出ない | contact point `kps-alertmanager` と root policy の provisioning を確認: `curl -u <admin>:<pass> http://grafana.local.test/api/v1/provisioning/contact-points` と `/api/v1/provisioning/policies`。Grafana ログの `logger=ngalert.notifier` でエラーを見る (AM の URL / API バージョン不一致など) |
| AM には居るのに Forgejo Issue が立たない | (a) bridge の稼働とログ: `kubectl -n monitoring logs deploy/am-forgejo-bridge`。(b) AM の route 設定: `charts/monitoring/values.yaml` の matchers (source=quality-agent / KubeJobFailed + ns 正規表現) にラベルが合っているか。(c) PAT: `am-forgejo-bridge-token` Secret の存在と write:issue スコープ。Forgejo API が 401/403 を返すと bridge は 500 応答で AM がリトライし続ける |
| Issue がコメントで溢れる | 想定外 (bridge は firing 集合が同じ通知を吸収する)。fps マーカー (`<!-- am-forgejo-bridge fps:... -->`) が body から消えていないか確認。Issue body を手動編集するとマーカーが壊れて重複コメントの原因になる |
| quality-run-stale が誤発火 | nightly CronJob の失敗を疑う: `kubectl -n quality-agent get jobs` → 直近 Job のログ。スコア未保存なら DB 接続 (quality-pg Secret) を確認 |
