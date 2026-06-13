# secrets/ — SealedSecret 置き場

このディレクトリには **SealedSecret (暗号文) のみ**を置く。平文 Secret は絶対に置かない。
`secrets` Application が一括 apply し、各ファイルが自身の `metadata.namespace` を持つので
1 Application で複数 namespace に配れる。作成・更新のワークフローは
[docs/runbooks/sealed-secrets.md](../docs/runbooks/sealed-secrets.md) を参照。

> [!NOTE]
> 公開ミラー ([docs/runbooks/public-mirror.md](../docs/runbooks/public-mirror.md)) では
> このディレクトリの `*.yaml` は防御的に除外され、この README だけが残る。
> 暗号文は master key 無しで復号不能だが、鍵漏洩時に遡って復号されるリスクを避けるため。

## 一覧

| ファイル | Secret 名 | namespace | 用途 |
| :--- | :--- | :--- | :--- |
| `am-forgejo-bridge-token.yaml` | `am-forgejo-bridge-token` | monitoring | am-forgejo-bridge が Issue 起票に使う Forgejo PAT (write:issue) |
| `backup-gpg-passphrase.yaml` | `backup-gpg-passphrase` | sealed-secrets | sealed-secrets 鍵バックアップの GPG 対称暗号パスフレーズ (パスワードマネージャと二重管理) |
| `forgejo-admin.yaml` | `forgejo-admin-secret` | forgejo | Forgejo admin アカウント (chart の `gitea.admin.existingSecret`) |
| `forgejo-pg.yaml` | `forgejo-pg` | forgejo | Forgejo → pg-main の DB 接続 |
| `forgejo-read.yaml` | `forgejo-read` | quality-agent | quality-agent の Forgejo API 読み取り PAT (read:repository + read:issue) |
| `forgejo-repo.yaml` | `forgejo-repo` | argocd | ArgoCD の repo credential (PAT 失効時は鶏卵問題 → runbook 参照) |
| `grafana-admin.yaml` | `grafana-admin` | monitoring | Grafana admin アカウント |
| `grafana-pg-monitoring.yaml` / `grafana-pg-postgres.yaml` | `grafana-pg` | monitoring / postgres | Grafana バックエンド DB のロール (利用側と CNPG 側の 2 namespace に配布) |
| `quality-pg-postgres.yaml` / `quality-pg-quality-agent.yaml` | `quality-pg` | postgres / quality-agent | quality DB の owner ロール (同上) |
| `quality-ro-pg-monitoring.yaml` / `quality-ro-pg-postgres.yaml` | `quality-ro-pg` | monitoring / postgres | quality DB の読み取り専用ロール `quality_ro` (Grafana datasource 用) |
| `seaweedfs-s3.yaml` | `seaweedfs-s3` | seaweedfs | SeaweedFS S3 の admin identity (`existingConfigSecret`) |
| `sonarqube-app.yaml` | `sonarqube-app` | sonarqube | SonarQube アプリケーション資格情報 |
| `sonarqube-pg-postgres.yaml` | `sonarqube-pg` | postgres | SonarQube バックエンド DB のロール |
| `sonarqube-token.yaml` | `sonarqube-token` | quality-agent | SonarQube 解析・API トークン |
| `velero-credentials.yaml` | `velero-credentials` | velero | Velero → SeaweedFS S3 の credential |
