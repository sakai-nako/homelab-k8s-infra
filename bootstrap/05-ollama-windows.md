# Phase 3 - Step 5: Windows ネイティブ Ollama のセットアップ

[README](../README.md) / [docs/architecture.md](../docs/architecture.md#ollama-windows-ネイティブ-接続) の前提知識を持つ前提。

このドキュメントは **GitOps の輪の外** で行う Windows ホスト側の手動手順を扱う。Ollama は RTX 5080 (16GB) を GPU パススルー無しで最大限活かすため、k3d/WSL2 の中ではなく **Windows ホスト上でネイティブに**動かす。クラスタ (Phase 4 の quality-agent) からは ConfigMap `ollama-external` の接続情報 (Windows ホストの LAN IP) を env として読み、HTTP 接続する。

## ゴール

- Windows ネイティブの Ollama が `:11434` で稼働し、GPU を使っている
- WSL2 / k3d クラスタ内の pod から Windows ホストの `:11434` に到達できる
- `manifests/ollama-external/ollama-endpoint.yaml` (ConfigMap) に正しいホスト IP が入っている
- クラスタ内 pod から `$OLLAMA_BASE_URL/api/tags` がモデル一覧を返す

> このステップは Phase 4 (quality-agent) で Ollama を実際に呼ぶ直前までに済んでいればよい。Phase 3 では接続情報 ConfigMap `ollama-external` だけ GitOps に入っており、Ollama 未導入/IP 未確定の間は到達できないだけで正常。

---

## 前提

- Windows 11 + NVIDIA ドライバ (RTX 5080 = Blackwell。CUDA 12.8+ 対応の比較的新しいドライバ R570 以降)
- `local-infra` WSL2 ディストリと k3d クラスタが稼働済み (Phase 1〜2 完了)
- GPU は ComfyUI 等とも共有する点に留意 (末尾「GPU 共有の注意」)

---

## Step 1: Ollama のインストール

[https://ollama.com/download/windows](https://ollama.com/download/windows) から Windows 版をインストールする。インストール後、PowerShell で確認:

```powershell
ollama --version
nvidia-smi          # RTX 5080 が見えること
```

## Step 2: WSL/コンテナから到達できるよう bind を 0.0.0.0 に

Ollama は既定で `127.0.0.1:11434` バインドのため、このままだと WSL2/k3d から見えない。**全インタフェース (`0.0.0.0`) で listen させる**必要がある。

### 方法 A (推奨): GUI トグル

新しい Ollama Windows アプリには Settings に **「Expose Ollama to the network」** トグルがある。これを **ON** にするのが正攻法 (= `0.0.0.0` で listen)。アプリが Firewall ルールも面倒を見てくれる場合があるため、Step 4 が不要になることもある。ついでに同 Settings で:

- **「Cloud」(クラウドモデル + Web 検索) は OFF** にする。設計三原則 #1「クラウド依存ゼロ」のため、private repo を解析する agent が誤って外部送信しない構成にする。
- 「Auto-download updates」は ON のままで可 (Blackwell/CUDA 対応が新しく保たれる)。
- 「Context length」はデフォルト (4k) で可。quality-agent は API の `num_ctx` でリクエスト毎に指定する。上げると毎ロードで VRAM (KV キャッシュ) を食う。

### 方法 B (CLI): 環境変数

GUI トグルが無い版では環境変数で設定する:

```powershell
[Environment]::SetEnvironmentVariable("OLLAMA_HOST", "0.0.0.0", "User")
```

### 確認

いずれの方法でも、Ollama (タスクトレイ常駐) を一度終了して再起動し、`netstat -ano | Select-String 11434` で `0.0.0.0:11434` を LISTEN していることを確認する。

## Step 3: モデルの取得

第一候補は **Qwen3 14B** (英日・コード理解が強く、Q4 で約 10GB と 16GB VRAM に余裕で収まる):

```powershell
ollama pull qwen3:14b
ollama run qwen3:14b "こんにちは"   # 動作確認 (nvidia-smi で GPU 使用を確認)
```

### VRAM とモデル規模 (RTX 5080 16GB)

| モデル | VRAM (Q4) | 所感 |
| :--- | :--- | :--- |
| **qwen3:14b** ◎ | ~10GB | 全レイヤ GPU 常駐・バランス最良。第一推奨 |
| qwen3:8b | ~5GB | ComfyUI と同時稼働させたいならこちら (VRAM 共存しやすい) |
| qwen3:30b-a3b (MoE) | ~18GB | 3B アクティブで高速だが 16GB 超で一部 CPU オフロード |
| qwen3:32b | ~20GB | 16GB に収まらず大幅オフロード。非推奨 |

### Qwen3 の思考モードに注意

Qwen3 はハイブリッド推論で、既定だと `<think>...</think>` の思考過程を長く出す。quality-agent の **分類・構造化出力 (sentiment を JSON で返す等) では思考オフが望ましい** (レイテンシ増・パース困難を避ける):

- 分類/抽出タスク → API で `"think": false`、対話なら `/set nothink`、またはプロンプト末尾に `/no_think`
- 複雑な要件↔テストの意味照合など → 思考オンの方が精度が上がる場合あり

Phase 4 で `quality-agent/prompts/` を書く際に、タスクごとの think 制御を明示する。

> モデルは後から `ollama pull gemma2:9b` 等に差し替え可能。ただし quality-agent のスコアは時系列の相対比較なので、モデル変更時は記録を残す ([docs/quality-model.md](../docs/quality-model.md) 参照)。

## Step 4: Windows Defender Firewall で 11434 を許可

> Step 2 の方法 A (GUI トグル) を使った場合、アプリが Firewall ルールを自動追加していることがある。先に Step 5 のプローブを試し、到達できればこの Step は不要。届かなければ以下を実施する。

WSL2 の仮想 NIC から来る inbound 11434 を許可する。LAN 全体には開けない方針:

```powershell
New-NetFirewallRule -DisplayName "Ollama 11434 from WSL" `
  -Direction Inbound -Action Allow -Protocol TCP -LocalPort 11434 `
  -InterfaceAlias "vEthernet (WSL)"
```

(`Get-NetAdapter` で WSL 用 vEthernet のエイリアス名を確認して合わせる。)

## Step 5: クラスタから到達できる IP を確定し ConfigMap に入れる

> 接続は **selectorless Service + Endpoints ではなく ConfigMap `ollama-external`** に集約する。理由は ArgoCD がデフォルトで `Endpoints`/`EndpointSlice` を `resource.exclusions` で除外し、raw Endpoints が同期されないため (docs/architecture.md「Ollama (Windows ネイティブ) 接続」参照)。

### 5-1. ⚠️ 採用したのは「Windows ホストの LAN IP」(NAT GW でも mirrored でもない)

クラスタ → Windows ホストの到達 IP には、2026-05-30 の検証で塞がった代替を消去した末、**Windows ホストの LAN IP** (例: `192.168.0.50`) を使う。理由を残す:

| 候補 | 結果 |
| :--- | :--- |
| NAT ゲートウェイ IP (`ip route show default` の gw、例 `172.22.32.1`) | ✅ 届くが **WSL 再起動で Hyper-V がサブネットを振り直すと変わりうる**ため不採用 |
| `host.k3d.internal` | ❌ k3d ノードコンテナから見て **docker bridge GW (`172.18.0.1`) を指し**、その先の Windows ホストへ届かない (pod から `getent hosts` + `curl` で確認) |
| `networkingMode=mirrored` | ❌ **k3d の公開ポート (API 6443→host / ingress 80,443) を壊し** kubectl が `dial tcp 0.0.0.0:<port>: i/o timeout` で不通になる (ロールバック済) |
| **Windows ホストの LAN IP** (例 `192.168.0.50`) | ✅ **採用**。後述の通り WSL 再起動に強い |

**なぜ LAN IP が安定か**: LAN IP は Windows の物理 NIC のアドレスで、WSL / Hyper-V の都合では一切変わらない。NAT GW IP が再割り当てで変わっても、pod → Windows の経路は `pod → docker bridge → WSL2 VM → (WSL の default route = 新しい GW) → Windows が LAN IP 宛をローカル配送` となり、**GW IP の変化を WSL の routing が吸収**する。つまり ConfigMap 側は LAN IP 固定のままでよい。ルーターで **DHCP 予約 (MAC 固定割り当て)** すれば恒久固定になる。

> ⚠️ mirrored の二次被害メモ: 連続 `wsl --shutdown` は docker の port-publishing 状態を壊し、`k3d-home-serverlb` が docker network 未接続になってクラスタ全体が触れなくなることがある。その場合 (`docker inspect k3d-home-serverlb --format '{{json .NetworkSettings.Networks}}'` が `{}`)、`docker stop k3d-home-serverlb && docker network connect k3d-home k3d-home-serverlb && docker start k3d-home-serverlb` でポート再 publish され復旧する (sudo 不要)。

### 5-2. Windows の LAN IP を調べ、pod から到達確認する

Windows 側 (PowerShell) で物理 NIC の IPv4 を確認:

```powershell
Get-NetIPAddress -AddressFamily IPv4 |
  Where-Object { $_.PrefixOrigin -in 'Dhcp','Manual' -and $_.InterfaceAlias -notmatch 'WSL|Loopback' } |
  Select-Object IPAddress, InterfaceAlias
# 例: 192.168.0.50  Ethernet   ← これを使う
```

クラスタ内 pod から、その LAN IP でモデル一覧 (JSON) が返ることを確認 (WSL 内):

```bash
kubectl -n quality-agent run ollama-probe --rm -i --restart=Never --quiet \
  --image=curlimages/curl --command -- \
  curl -s --max-time 6 http://192.168.0.50:11434/api/tags
```

> 届かない場合は Firewall (Step 4) を疑う。LAN プロファイルからの inbound 11434 が許可されている必要がある。Ollama Windows アプリは既定で `ollama.exe` に対し全ポート Allow の inbound ルールを入れることがあり、その場合は LAN からそのまま届く (本環境はこれで疎通)。露出 scope を絞りたい場合は後述「Firewall scope の注意」を参照。

### 5-3. 確定した IP を Git に反映

`manifests/ollama-external/ollama-endpoint.yaml` (ConfigMap) の `OLLAMA_BASE_URL` / `OLLAMA_HOST` を LAN IP に差し替え、commit & push する。ArgoCD が自動 sync する。**マシン依存箇所はこの 1 ファイルだけ**で、しかも LAN IP 固定なので WSL 再起動では触らずに済む (買い替え時のみ更新)。

### 5-4. Firewall scope の注意

Ollama Windows アプリが入れる inbound ルールは `ollama.exe` に対し **Public プロファイル / 全ポート Allow** のことがある (本環境で確認)。これは同一 LAN 上の他デバイスからも `:11434` に到達できる状態を意味する。家庭内 LAN なら許容範囲だが、露出を絞りたい場合は ollama.exe ルールを無効化し、サブネット限定の明示ルールに置き換える:

```powershell
# 例: 自宅 LAN サブネットからのみ 11434 を許可 (RemoteAddress を環境に合わせる)
New-NetFirewallRule -DisplayName "Ollama 11434 from home LAN" `
  -Direction Inbound -Action Allow -Protocol TCP -LocalPort 11434 `
  -RemoteAddress 192.168.0.0/24
```

## Step 6: 到達確認

ConfigMap の値を使ってクラスタ内 pod から到達を確認する:

```bash
URL=$(kubectl -n quality-agent get configmap ollama-external -o jsonpath='{.data.OLLAMA_BASE_URL}')
kubectl -n quality-agent run ollama-probe --rm -i --restart=Never --quiet \
  --image=curlimages/curl --command -- \
  curl -s --max-time 6 "$URL/api/tags"
```

`qwen3:14b` を含む JSON が返れば、Phase 4 の quality-agent から Ollama を使える状態。

---

## GPU 共有の注意

GPU は RTX 5080 の 1 枚を Ollama と ComfyUI 等で共有する:

- Flux 系画像生成は 12〜16GB、SDXL でも 6〜10GB、14B LLM (Q4) は ~10GB VRAM を使う
- **両者の同時フル稼働は OOM する**。時分割で使うか、片方を小さいモデルにする
- 使わない間に Ollama が VRAM を抱え続けないよう、必要なら `OLLAMA_KEEP_ALIVE` を短く設定する (例: `5m`)

---

ここまで完了したら、Ollama 連携は Phase 4 の quality-agent 実装で利用する。
