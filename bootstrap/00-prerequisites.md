# Phase 1 - Step 0: 前提環境のセットアップ

[README](../README.md) / [docs/architecture.md](../docs/architecture.md) の前提知識を持つ前提。

このドキュメントは **GitOps の輪に入る前** の手動セットアップ手順を扱う。新しい Windows マシンでもこの手順を踏むだけで Phase 1 Bootstrap が始められる状態に整える。

## ゴール

- Windows 上に **local-infra 専用の WSL2 ディストリ** が存在する
- そのディストリ内に Docker Engine / kubectl / helm / k3d が入っている
- NAS が `/mnt/nas` に SMB マウントされている
- `k3d cluster create test` と `docker run hello-world` が成功する

ここまで来たら次は [`01-k3d-cluster.yaml`](01-k3d-cluster.yaml) に進む。

---

## 前提

- Windows 11 (バージョン 22H2 以降推奨)
- 管理者権限のある PowerShell
- メモリ 32GB 以上 (24GB を WSL2 に割り当てる前提)
- NAS の SMB 共有パス / アカウント / パスワードを把握済み

---

## Step 1: Podman Desktop の片付け (該当する場合のみ)

local-infra では Docker Engine を WSL2 に直接インストールするため、Podman Desktop は不要。試しに入れただけなら削除する。

### 1-1. Podman Desktop に残しておくべきコンテナ・ボリュームがないかを確認

PowerShell で:

```powershell
podman ps -a                # 全コンテナ
podman images               # 全イメージ
podman volume ls            # 全ボリューム
```

何も無い、または捨てて良いものだけならスキップして 1-2 へ。残したいものがあれば:

```powershell
# イメージ保存例
podman save -o C:\Backup\my-image.tar my-image:tag
# ボリューム保存例
podman volume export myvol -o C:\Backup\myvol.tar
```

### 1-2. Podman Desktop アンインストール

1. Windows 設定 → アプリ → インストールされているアプリ
2. **Podman Desktop** を検索 → アンインストール
3. **Podman** (CLI 本体) も別エントリで残っている場合があるので同様にアンインストール

### 1-3. Podman の WSL2 VM を削除

Podman Desktop は裏で `podman-machine-default` という WSL2 ディストリを作る。残骸を消す:

```powershell
wsl --list --verbose
# podman-machine-default が居たら:
wsl --unregister podman-machine-default
```

`%USERPROFILE%\.local\share\containers\` などにキャッシュが残っている場合があるので不要なら削除。

---

## Step 2: 専用 WSL2 ディストリの作成

local-infra 専用に Ubuntu ディストリを新規作成する。既存の Ubuntu があっても汚さないため別名で入れる。

### 2-1. WSL2 が有効か確認

```powershell
wsl --status
wsl --version
```

無効の場合:

```powershell
wsl --install --no-distribution
# 再起動が要求されたら従う
wsl --set-default-version 2
```

### 2-2. Ubuntu 24.04 を別ディストリ名で作成

公式の Ubuntu-24.04 アプリは「Ubuntu-24.04」という固定名を取る。**local-infra 専用**として別名にしたいので、`wsl --import` で命名する:

```powershell
# 1. 公式 Ubuntu-24.04 のベースイメージを一旦インストール
wsl --install -d Ubuntu-24.04
# 初回起動時にユーザー名 (例: sakai) / パスワード設定 → 完了後 exit で抜ける

# 2. tar export
wsl --shutdown
mkdir C:\WSL\local-infra
wsl --export Ubuntu-24.04 C:\WSL\local-infra-base.tar

# 3. 別名で import (これが local-infra 専用)
wsl --import local-infra C:\WSL\local-infra C:\WSL\local-infra-base.tar --version 2

# 4. 元の Ubuntu-24.04 が不要なら削除 (他用途で使うなら残してOK)
wsl --unregister Ubuntu-24.04
del C:\WSL\local-infra-base.tar
```

### 2-3. デフォルトユーザーを設定

`wsl --import` で作ったディストリは root 起動になる。一般ユーザーを既定にする:

```powershell
wsl -d local-infra -u root
# 以下は WSL 内で実行
```

```bash
# WSL 内
USERNAME=sakai
adduser $USERNAME
usermod -aG sudo $USERNAME
echo -e "[user]\ndefault=$USERNAME" >> /etc/wsl.conf
exit
```

```powershell
# Windows 側
wsl --terminate local-infra
wsl -d local-infra
# プロンプトが sakai@... になっていれば OK
```

### 2-4. 既定ディストリにする (任意)

`wsl` とだけ叩いた時に local-infra が起動するように:

```powershell
wsl --set-default local-infra
```

---

## Step 3: `.wslconfig` でリソース割当

`C:\Users\<ユーザー名>\.wslconfig` を作成 (既存なら編集):

```ini
[wsl2]
memory=24GB
processors=8
swap=8GB
localhostForwarding=true

[experimental]
autoMemoryReclaim=gradual
sparseVhd=true
```

反映:

```powershell
wsl --shutdown
wsl -d local-infra
```

---

## Step 4: Ubuntu の初期更新と基礎パッケージ

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y \
  ca-certificates curl gnupg lsb-release \
  cifs-utils \
  jq git make build-essential \
  bash-completion
```

---

## Step 5: Docker Engine のインストール (Docker Desktop なし)

公式 apt リポジトリから直接入れる方式。Docker Desktop は不要。

```bash
# キーリング
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
  sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

# リポジトリ
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# rootless でなく一般ユーザーから sudo 無しで叩けるように
sudo usermod -aG docker $USER
```

WSL2 では `systemd` がデフォルトで動いてないと dockerd が起動しないので、`/etc/wsl.conf` に追加:

```bash
sudo tee -a /etc/wsl.conf > /dev/null <<'EOF'

[boot]
systemd=true
EOF
```

```powershell
# Windows 側で再起動
wsl --terminate local-infra
wsl -d local-infra
```

動作確認:

```bash
sudo systemctl status docker
docker run --rm hello-world
```

`Hello from Docker!` が出れば OK。

### 5-b. Forgejo レジストリ用の insecure-registry 設定

クラスタ稼働後、自作イメージ (quality-agent 等) を Forgejo のコンテナレジストリへ `docker push` するための設定。Forgejo は `http://forgejo.local.test` (TLS なし) で公開されるため、HTTP レジストリとして明示する必要がある:

```bash
sudo tee /etc/docker/daemon.json > /dev/null <<'EOF'
{
  "insecure-registries": ["forgejo.local.test"]
}
EOF
sudo systemctl restart docker
```

push 手順と k3d ノード側の pull 経路は [docs/runbooks/forgejo-registry.md](../docs/runbooks/forgejo-registry.md) を参照。

---

## Step 6: kubectl / helm / k3d のインストール

### kubectl

```bash
curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
sudo install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl
rm kubectl
kubectl version --client
```

### helm

```bash
curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
helm version
```

### k3d

```bash
curl -s https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh | bash
k3d version
```

### bash 補完 (任意)

```bash
echo 'source <(kubectl completion bash)' >> ~/.bashrc
echo 'source <(helm completion bash)' >> ~/.bashrc
echo 'source <(k3d completion bash)' >> ~/.bashrc
echo 'alias k=kubectl' >> ~/.bashrc
echo 'complete -F __start_kubectl k' >> ~/.bashrc
source ~/.bashrc
```

### Starship プロンプト (任意)

開発体験のために 2 行プロンプト + git / cmd_duration 表示等を有効にしたい場合 (Windows 側で同じ Starship を使っているなら設定共有も可能):

```bash
# Starship インストール
curl -sS https://starship.rs/install.sh | sh

# bash 統合
echo 'eval "$(starship init bash)"' >> ~/.bashrc

# (任意) Windows 側に既に starship.toml がある場合はシンボリックリンクで同期
mkdir -p ~/.config
ln -s /mnt/c/Users/sakai/.config/starship.toml ~/.config/starship.toml

# 反映
exec bash
```

VS Code 統合ターミナルや Windows Terminal で記号が豆腐 (☐) になる場合は、フォント設定を **Nerd Font** 系 (FiraCode Nerd Font 等) に変更する。

---

## Step 7: NAS の SMB マウント

NAS が `\\nas.local\share` で公開されている前提 (実際のホスト名・共有名に置き換え)。

### 7-1. 認証情報ファイル

平文を `/etc/fstab` に書かないため別ファイル化:

```bash
sudo tee /etc/cifs-creds > /dev/null <<'EOF'
username=YOUR_NAS_USER
password=YOUR_NAS_PASSWORD
domain=WORKGROUP
EOF
sudo chmod 600 /etc/cifs-creds
```

### 7-2. マウントポイント作成と /etc/fstab エントリ

NAS の IP を直書きする。WSL2 は起動時に `/etc/hosts` を自動再生成する仕様のため、ホスト名で書くと毎回切れる。NAS 側で静的 IP を固定している前提 (DHCP 配布だと IP 変動時に再マウントが必要)。

```bash
sudo mkdir -p /mnt/nas

# UID/GID は `id` で確認 (sakai なら通常 1000)
# NAS の IP は環境に合わせて置換
sudo tee -a /etc/fstab > /dev/null <<'EOF'

# local-infra NAS backup target
//192.168.0.40/disk1 /mnt/nas cifs credentials=/etc/cifs-creds,uid=1000,gid=1000,iocharset=utf8,nofail,_netdev,x-systemd.automount 0 0
EOF

sudo mount -a
ls /mnt/nas
```

NAS のディレクトリが見えれば OK。

### 7-2-b. (任意) /etc/wsl.conf で `/etc/hosts` 自動再生成を停止

`/etc/hosts` に手動エントリを書き続けたい場合 (NAS 以外にも複数のホストを名前解決したい等)、WSL2 の auto-generate を止める:

```bash
sudo tee -a /etc/wsl.conf > /dev/null <<'EOF'

[network]
generateHosts = false
generateResolvConf = true
EOF
```

次回の `wsl --terminate local-infra` → `wsl -d local-infra` から有効。fstab を IP 直書きにする方針なら必須ではない。

### 7-3. バックアップ用ディレクトリ作成

```bash
mkdir -p /mnt/nas/local-infra-backups/logical/forgejo
mkdir -p /mnt/nas/local-infra-backups/logical/postgres
mkdir -p /mnt/nas/local-infra-backups/logical/grafana
mkdir -p /mnt/nas/local-infra-backups/logical/argocd
mkdir -p /mnt/nas/local-infra-backups/velero
mkdir -p /mnt/nas/local-infra-backups/keys
```

---

## Step 8: 動作確認チェックリスト

すべて WSL 内 (`local-infra` ディストリ) で実行:

```bash
# 1. Docker
docker run --rm hello-world                     # → Hello from Docker!

# 2. k3d (お試しクラスタ起動 → 削除)
k3d cluster create smoke-test --servers 1 --agents 1
kubectl get nodes                                # → 2 ノード Ready
k3d cluster delete smoke-test

# 3. helm
helm repo add bitnami https://charts.bitnami.com/bitnami
helm search repo bitnami/postgresql | head      # → 1 行以上ヒット
helm repo remove bitnami

# 4. NAS マウント
echo "test-$(date)" > /mnt/nas/local-infra-backups/.writetest
cat /mnt/nas/local-infra-backups/.writetest
rm /mnt/nas/local-infra-backups/.writetest

# 5. systemd
systemctl is-system-running                      # running または degraded (degraded でも実害なければ OK)
```

すべて通過したら Phase 1 Step 1 (k3d クラスタ本番起動) に進める状態。

---

## トラブルシュート

| 症状 | 対処 |
| :--- | :--- |
| `wsl --import` で `0x80370102` | 仮想化が BIOS で無効。BIOS で Intel VT-x / AMD-V を有効化 |
| `dockerd` が起動しない | `/etc/wsl.conf` の `[boot] systemd=true` が反映されているか確認 (`ps -p 1 → systemd` であるべき)。`wsl --shutdown` → 再起動 |
| `mount -a` でハング | NAS が SMB1 のみ対応の古い機種。`vers=2.0` を fstab オプションに追加 (例: `cifs vers=2.0,credentials=...`) |
| `permission denied` で NAS 書込不可 | `uid=`/`gid=` が WSL ユーザーの id と一致しているか `id` で確認 |
| `k3d cluster create` で iptables エラー | カーネルモジュール不足。`sudo modprobe br_netfilter` でロード、永続化は `/etc/modules-load.d/k3d.conf` に `br_netfilter` を記載 |
| メモリが思ったより食われる | `.wslconfig` の `autoMemoryReclaim=gradual` が効くまで数分かかる。`wsl --shutdown` でリセット |

---

## 次のステップ

- [`01-k3d-cluster.yaml`](01-k3d-cluster.yaml) — k3d 本番クラスタ定義
- [`02-forgejo-install.md`](02-forgejo-install.md) — Forgejo + SQLite の手動 install + push
- [`03-argocd-install.md`](03-argocd-install.md) — ArgoCD 手動 install (repoURL は Forgejo 内部 Service)
- [`04-root-app.yaml`](04-root-app.yaml) — App-of-Apps の root Application
