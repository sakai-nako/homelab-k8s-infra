# 定期ジョブ一覧 (1 日のタイムライン)

クラスタで動く**全定期バッチを 1 箇所で見渡す**ためのドキュメント。新しい CronJob を足すとき・時刻をずらすときは、まずここで空き時間と順序依存を確認し、変更後は本書の表も更新する。

個々のジョブの設計意図は [backup.md](backup.md) (バックアップ系) と [runbooks/sonarqube-scan.md](runbooks/sonarqube-scan.md) / [runbooks/quality-observability.md](runbooks/quality-observability.md) (品質評価系) を参照。

---

## 1 日のタイムライン (JST 順)

| 時刻 (JST) | 周期 | ジョブ | namespace | 何をするか | 出力先 / 保持 | TZ 指定 | 定義ファイル |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 03:00 | 毎日 | `forgejo-dump` | forgejo | `forgejo dump` (リポジトリ・Issue・添付・LFS・DB ダンプ込み) | NAS `logical/forgejo/forgejo-<TS>.zip` / 14 日 | Asia/Tokyo | [manifests/backups/forgejo-dump-cronjob.yaml](../manifests/backups/forgejo-dump-cronjob.yaml) |
| 03:15 | 毎日 | `postgres-dump` | postgres | `pg_dumpall --clean --if-exists` (pg-main 全 DB + ロール) | NAS `logical/postgres/pg-main-<TS>.sql.gz` / 14 日 | Asia/Tokyo | [manifests/backups/postgres-dump-cronjob.yaml](../manifests/backups/postgres-dump-cronjob.yaml) |
| 03:45 | 毎週日曜 | `sealed-secrets-key-backup` | sealed-secrets | 歴代全 sealing key を GPG 対称暗号化 + ラウンドトリップ復号検証 | NAS `keys/sealed-secrets-master-<TS>.gpg` / 全世代 | Asia/Tokyo | [manifests/backups/sealed-secrets-key-backup-cronjob.yaml](../manifests/backups/sealed-secrets-key-backup-cronjob.yaml) |
| 05:15 | 毎日 | `sonar-scan-nightly` | quality-agent | local-infra を clone し sonar-scanner で解析を SonarQube に投入 | SonarQube (バックエンドは pg-main) | Asia/Tokyo | [manifests/quality-agent/sonar-scan-cronjob.yaml](../manifests/quality-agent/sonar-scan-cronjob.yaml) |
| 06:00 | 毎日 | `quality-agent-nightly` | quality-agent | collect → analyze → score → report (initContainer で clone + Macaron SLSA 監査) | pg-main の quality DB (時系列) | Asia/Tokyo | [manifests/quality-agent/cronjob.yaml](../manifests/quality-agent/cronjob.yaml) |
| 07:00 | 毎月 1 日 | `backup-restore-test` | backups | 最新 pg ダンプを使い捨て PostgreSQL に実復元して DB/行数検証 + forgejo zip CRC + 鍵鮮度 (<8 日) | 検証のみ (失敗時 Issue 起票) | Asia/Tokyo | [manifests/backups/restore-test-cronjob.yaml](../manifests/backups/restore-test-cronjob.yaml) |
| 13:00 (= 04:00 UTC) | 毎日 | Velero Schedule `daily` | velero | クラスタリソース + PV (kopia FSB) を SeaweedFS の S3 バケットへ | SeaweedFS `velero` バケット / TTL 720h (30 日) | **UTC** (Velero 既定) | [charts/velero/values.yaml](../charts/velero/values.yaml) |
| 14:00 (= 05:00 UTC) | 毎日 | `velero-s3-nas-export` | velero | velero バケットを `aws s3 sync` → tar.gz で NAS へ退避 | NAS `velero/velero-<TS>.tar.gz` / 30 日 | **UTC** (timeZone 未指定) | [manifests/velero-export/export-cronjob.yaml](../manifests/velero-export/export-cronjob.yaml) |

全 CronJob とも `concurrencyPolicy: Forbid` (重複起動なし)。バックアップ系は冒頭で NAS (`/mnt/nas`) の書き込み可否を pre-check し、SMB 断は即 Job 失敗として顕在化する。

## 順序依存 (時刻を動かすときはここを崩さない)

1. **層 1 (03:00 → 03:15 → 03:45)**: 同じ NAS マウントに書くため軽くずらしてある。相互のデータ依存は無い。
2. **sonar-scan-nightly (05:15) → quality-agent-nightly (06:00)**: SonarQube の解析結果が quality-agent の maintainability collector の入力。45 分は解析完了の待ち余裕。
3. **backup-restore-test (07:00)**: 当日 03:00 / 03:15 に取れたばかりの最新ダンプを検証する前提の時刻。
4. **Velero daily (13:00 JST) → velero-s3-nas-export (14:00 JST)**: export は当日の Velero バックアップ完了後に走る。
5. 失敗時の通知はどれも共通: KubeJobFailed アラート → Alertmanager → am-forgejo-bridge → Forgejo Issue 自動起票。

## タイムゾーンの罠

- k8s CronJob は `timeZone: Asia/Tokyo` を明示したものと、**未指定 (= UTC 解釈)** の `velero-s3-nas-export` が混在している。Velero の Schedule CR も UTC 評価。
- **新しい CronJob を足すときは `timeZone: Asia/Tokyo` を明示する**こと (未指定だと意図より 9 時間遅れ/早まりで他ジョブと衝突しうる)。

## 定期だが Job ではないもの (イベント駆動・常駐評価)

| 周期 / トリガ | 仕組み | 内容 |
| :--- | :--- | :--- |
| push 後 〜30 秒 | ArgoCD auto-sync (webhook + selfHeal) | Git の変更をクラスタへ反映 |
| 10 分間隔 | Grafana unified alerting (ルール 4 本) | quality DB を SQL 評価し劣化検知 → Alertmanager へ転送 |
| group_wait 1m〜5m / repeat 24h | Alertmanager route `forgejo-issues` | 品質劣化 (source=quality-agent) と KubeJobFailed を am-forgejo-bridge へ。repeat 再通知は bridge が吸収 |
| 常時 | trivy-operator | 全 namespace のワークロードを継続 CVE/IaC スキャン (quality-agent が nightly で集計) |
| 約 30 日周期 | sealed-secrets controller | sealing key の自動ローテーション (新鍵追加)。週次の鍵バックアップが毎回**歴代全量**を回収するので取りこぼし無し |

## 自動化していない定期作業 (人間のカレンダー側)

| 周期 | 作業 | 参照 |
| :--- | :--- | :--- |
| 月 1 | 論理ダンプの外付け HDD コピー | [backup.md](backup.md#バックアップが壊れないための運用) |
| 半年に 1 回 | DR dry-run (鍵復号 + pg 復元は必須) + GPG パスフレーズの実在確認 | [runbooks/disaster-recovery.md](runbooks/disaster-recovery.md#訓練と検証の運用) |

---

## 関連ドキュメント

- [backup.md](backup.md) — バックアップ各ジョブの設計意図と NAS レイアウト
- [runbooks/disaster-recovery.md](runbooks/disaster-recovery.md) — バックアップの所在一覧と復旧手順
- [runbooks/quality-observability.md](runbooks/quality-observability.md) — アラート評価・通知経路の運用
- [runbooks/sonarqube-scan.md](runbooks/sonarqube-scan.md) — nightly 解析 2 段構成の詳細
