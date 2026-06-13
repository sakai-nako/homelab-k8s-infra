# Runbook: GitHub 公開ミラー

このリポジトリの「公開可能な部分」を GitHub の public リポジトリへ任意のタイミングで push するための運用手順。
目的は、自宅でやっていること (GitOps 構成・品質評価フレームワーク) を外部の人に説明できるようにすること。

## 方式: フィルタ済みスナップショット (履歴は公開しない)

[tools/publish-public-mirror.ps1](../../tools/publish-public-mirror.ps1) が HEAD の内容を
`git archive` で取り出し、フィルタを適用したスナップショットをミラーリポジトリ
(既定: `..\local-infra-public`) に「1 回の publish = 1 コミット」として積む。
private 側のコミット履歴そのものは公開しない。

**履歴を公開しない理由**: 履歴ごと公開 (`git filter-repo` 等) すると、過去の全コミットの
diff・メッセージに機密が無いことを公開のたびに保証し続ける必要がある。スナップショット方式なら
保証対象が「現在の HEAD × フィルタルール」だけに閉じ、将来のコミットで何が入っても公開面に
影響しない。ミラー側には publish ごとの差分履歴が独立に積まれるので、公開後の変遷は追える。

## 何がフィルタされるか

ルールは [tools/public-mirror-rules.psd1](../../tools/public-mirror-rules.psd1) に集約
(**このルールファイル自体も実値を含むためミラーから除外される**)。3 層構成:

| 層 | 内容 | 現在のルール |
| :--- | :--- | :--- |
| 除外 | ファイルごとミラーに含めない | `secrets/*.yaml` (SealedSecret 暗号文)、ルールファイル自体 |
| 置換 | 実環境固有値を例示値に正規化 | Windows ホスト LAN IP、NAS IP |
| 禁止パターン検査 | 置換・除外の漏れを publish 直前に検出し、1 件でもあれば**ミラー未更新のまま中止** | 実 LAN IP の残留、個人ドメイン、`encryptedData` (暗号文)、秘密鍵、GitHub PAT 形式 |

加えて、公開側 README の冒頭に「フィルタ済みミラーである」注記と生成元コミット SHA を自動挿入する。

SealedSecret は master key が無ければ復号不能であり技術的には公開可能だが、鍵漏洩時に
全暗号文が遡って復号されるため防御的に除外する。ディレクトリの説明価値は
[secrets/README.md](../../secrets/README.md) (ミラーに残る) が担う。

## 公開先

**https://github.com/sakai-nako/homelab-k8s-infra** (2026-06-13 初回 publish 済み)。
github.com への push 認証は `gh auth setup-git` で gh CLI を credential helper にしてある
(Forgejo 向けの既存 helper 設定とは衝突しない)。

## 初回セットアップ (新マシン移行時に再実施)

```powershell
# 1. ローカルでミラーを生成して中身を確認 (push しない)
.\tools\publish-public-mirror.ps1
Get-ChildItem ..\local-infra-public -Recurse -File | Select-Object FullName

# 2. ミラーの origin に GitHub を登録し、push 認証を整える
git -C ..\local-infra-public remote add origin https://github.com/sakai-nako/homelab-k8s-infra.git
gh auth login
gh auth setup-git

# 3. 初回 push
.\tools\publish-public-mirror.ps1 -Push
```

## 日常運用

公開したいタイミングで 1 コマンド:

```powershell
.\tools\publish-public-mirror.ps1 -Push
```

- 公開対象は **HEAD のみ**。未コミットの変更は含まれない (警告が出る)。
- 同じ HEAD で再実行しても「差分なし」でスキップされる (冪等)。
- 禁止パターン検査で中止された場合は、該当箇所を確認してから
  ルール (置換 or 除外) を追加して再実行する。**検査を無効化して通さない**。

## 新しい機密クラスが増えたら

新しいコンポーネント追加時に以下を自問し、該当したら `tools/public-mirror-rules.psd1` を更新する:

1. **ファイルごと機密** (暗号文、credential 類) → `ExcludePaths` に追加
2. **実環境固有値が文書/マニフェストに散らばる** (IP、ホスト名、アカウント名) → `Replacements` に追加
3. **漏れたら困る形式が機械的に判定できる** (トークンの prefix 等) → `ForbiddenPatterns` に追加

原則: **置換・除外で対応し、禁止パターンは安全網**。禁止パターンだけに頼ると
ヒットするまで公開が止まらない。

## 注意事項

- **一度 push したものは取り消せない前提で運用する**。GitHub 上で force-push しても
  fork・キャッシュ・アーカイブに残りうる。publish 前のローカル確認 (`-Push` 無し実行) を習慣にする。
- 万一機密を push してしまった場合: 該当 credential を**即ローテーション** (PAT 再発行、
  パスワード変更) するのが第一対応。履歴の消去 (force-push) は事後の掃除でしかない。
- ミラーリポジトリ (`..\local-infra-public`) は生成物。手で編集しない
  (次回 publish の `robocopy /MIR` で消える)。
