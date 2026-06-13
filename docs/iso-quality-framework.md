# ISO/IEC 25010:2023 / 25019:2023 規格と本プロジェクトの理論的根拠

本プロジェクトが評価軸として採用する国際規格の概説と、AI エージェントによる自動品質評価という発想の理論的根拠を示す原典ドキュメント。

元は Gemini Deep Research による調査出力 (旧 `temp.md`) をベースに、本リポジトリの設計集と重複する詳細表を圧縮し、規格概説と参考文献を中心に再構成したもの。

- 実装ツールへの対応マッピング → [docs/quality-model.md](quality-model.md)
- システム構成 / GitOps 設計 → [docs/architecture.md](architecture.md)
- [README](../README.md)

---

## はじめに

ソフトウェアエンジニアリングにおける迅速なデリバリーと継続的デプロイメントの潮流は、システムの品質保証において新たな課題をもたらしている [1]。従来の人間主導による設計レビューや手動テストだけでは、大規模かつ高速化するコードベースの変更に対して網羅的な品質評価を行うことは困難である [2]。

本プロジェクトはこの課題に対し、Git リポジトリ内のコード・構成ファイル・CI/CD パイプライン、さらには Issue Tracker のメタデータを自律的に解析し、国際規格に準拠した多角的な品質評価を実行する AI エージェントを、ローカル環境で構築・運用することを目指す [4]。

評価軸として採用するのは、ISO/IEC SQuaRE (Systems and software Quality Requirements and Evaluation) シリーズの最新規格である:

- **ISO/IEC 25010:2023** — 製品品質モデル
- **ISO/IEC 25019:2023** — 利用時の品質モデル (Quality-in-Use; QiU)

SQuaRE 全体像は ISO/IEC 25002:2024 のメタモデルによって規定されており、製品の内部・外部ソースコード品質から、エンドユーザが体感する利用時の品質まで一貫した概念モデルでリンクされる [4][10]。

---

## ISO/IEC 25010:2023 製品品質モデル

### 2011 年版からの主要変更点

ISO/IEC 25010:2023 は、2011 年版における 8 つの大分類を、クラウドネイティブ環境・安全性重視のシステム・機械学習システム導入を反映して **9 特性へ再定義** した [3][12]。本プロジェクトの評価ロジックに影響する主要な変更:

| 変更点 | 内容 | 評価への影響 |
| :--- | :--- | :--- |
| **「安全性 (Safety)」の新設** | トップレベル特性として独立 | 医療・自動運転・エネルギー制御等の領域ではフェイルセーフ実装の有無を独立軸として評価する必要 [5][8] |
| **「移植性 (Portability)」→「柔軟性 (Flexibility)」** | 概念拡張。スケーラビリティを副特性として吸収 | クラウドネイティブ実装 (HPA・マニフェスト) 評価が柔軟性側に統合される [8][9] |
| **「操作性 (Usability)」→「相互作用性 (Interaction Capability)」** | UI だけでなく対話プロセス全般を評価対象に | a11y, インクルーシブ性, 自己記述性, ユーザアシスタンス等が拡張サブ特性として追加 [9][16] |
| **「セキュリティ:耐性 (Resistance)」の追加** | 攻撃進行中のサービス継続能力 | レートリミット, WAF, サーキットブレーカー等を実装監査対象に [9] |

### 9 特性の概要

(詳細マッピングと AI エージェントによる解析ロジックは [docs/quality-model.md](quality-model.md) を参照)

1. **機能適合性 (Functional Suitability)** — 機能網羅性 / 機能正確性 / 機能妥当性
2. **性能効率性 (Performance Efficiency)** — 時間特性 / 資源利用性 / 容量
3. **互換性 (Compatibility)** — 共存性 / 相互運用性
4. **相互作用性 (Interaction Capability)** — 適切性識別性 / 学習容易性 / 操作容易性 / ユーザエラー保護性 / ユーザエンゲージメント / インクルーシブ性 / ユーザアシスタンス / 自己記述性 [9]
5. **信頼性 (Reliability)** — 無欠陥性 / 稼働性 / フォールトトレランス / 回復性
6. **セキュリティ (Security)** — 機密性 / 完全性 / 否認防止性 / 責任追跡性 / 真正性 / 耐性 [9]
7. **保守性 (Maintainability)** — モジュール性 / 再利用性 / 解析性 / 修正性 / 試験性
8. **柔軟性 (Flexibility)** — 環境適応性 / **スケーラビリティ** / インストール容易性 / 置換容易性 [9]
9. **安全性 (Safety)** — 運用制約 / リスク識別 / フェイルセーフ / 危険警告 / 安全統合 [9]

### 新設・拡張特性の補足

#### 安全性 (Safety)

2023 年版で新設された安全性は、医療機器・航空宇宙・自動運転・エネルギー制御などの領域において、人命や財産、環境への重大な危害を防ぐための最重要指標である [5]。AI エージェントは、これらの物理的リスクを制御するコードが正しく配置されているかを判断する [9]。

- **フェイルセーフ構造**: 例外発生時にリソース (ファイル, シリアルポート, ネットワーク接続) をリークせず、強制的に Graceful Shutdown に入るための `finally` 句, Go の `defer`, C++ の RAII パターンの実装率 [6]
- **運用制約と安全統合**: Kubernetes 定義における resource limit, Seccomp / AppArmor 等サンドボックス化ポリシーの記述レベルの静的監査 [6]

#### 相互作用性 (Interaction Capability)

「操作性」からの概念拡張は、単に使いやすい UI ではなく **エンドユーザとの対話全般** の適正化を要求する [8]。

- **インクルーシブ性 / ユーザアシスタンス**: WAI-ARIA 属性が組み込まれているかの評価, axe-core による WCAG 準拠の CI 自動検証 [16]
- **自己記述性**: CLI / API のエラー時に適切なコマンド補完, ヘルプテキスト, 想定される次アクションを多言語かつ文脈に沿って提示するメカニズムの有無 [9]

#### セキュリティ:耐性 と 柔軟性:スケーラビリティ

- **耐性 (Resistance, 新設)**: 攻撃進行中であっても中核的なサービス機能を維持する能力。レートリミッター, WAF, 分散実行環境のパニック時自動再起動しきい値が評価対象 [9]
- **スケーラビリティ (柔軟性の副特性として追加)**: DB コネクションプール上限, ロードバランサ構成, メッセージキュー (Kafka 等) のパターン適用 [8]

---

## ISO/IEC 25019:2023 利用時の品質モデル

### Quality-in-Use (QiU) の本質

利用時の品質 (QiU) とは、システムが **特定の利用コンテキスト (Context of Use; CoU)** で使用された際に、対象のステークホルダーがどれだけ有益・安全かつ満足して目的を達成できるかを示す指標である [21]。

QiU はコードの静的評価では捉えきれず、運用データ・バグ追跡ログ・ユーザコミュニティの対話ログなどのメタ解析を必要とする [4]。

### 3 つの主要特性

ISO/IEC 25019:2023 は QiU を以下の 3 特性に整理する [18]:

```
利用時の品質モデル
├── 有益性 (Beneficialness)
│     ├── 有効性 (Effectiveness)
│     ├── 効率性 (Efficiency)
│     └── 満足性/ユーザビリティ (Usability/Satisfaction)
├── リスク回避性 (Freedom from Risk)
│     ├── 身体的健康・安全性リスク
│     ├── 経済的リスク
│     └── 環境・社会的リスク
└── 受容性 (Acceptability)
      ├── 信頼 (Trust)
      └── 受容・導入容易性 (Acceptance)
```

### 利用コンテキスト (CoU) の決定的重要性

QiU を評価するための前提条件として、システムが誰によって、どのような業務で使用されているかを示すコンテキストの事前定義が必要である [21]。

**コンテキストの前提が変更された場合、すべての QiU 要件定義と評価基準を再定義しなければならない** [21]。本プロジェクトでは、リポジトリ内に CoU メタドキュメント (YAML) を配備し、その変更を検知することで評価モデルを再構築する設計とする (実装詳細: [docs/quality-model.md](quality-model.md))。

### NLP マイニングによる定量化アプローチ

QiU の主観的特性を客観指標化するアプローチとして、Git リポジトリ周辺メタデータ (Issue, PR, フォーラム) の自然言語処理は学術的に確立されている [24]。

地球システム動力学をシミュレートする反応輸送モデル (RTM) のユーザフォーラム 3,941 件のディスカッションスレッドをマイニングした事例では、**有益性 (特にユーザビリティ) がエンドユーザに最大の不満を喚起していた** ことが統計的に判明している [24]。

本プロジェクトでも同様のアプローチを Ollama (ローカル LLM) で実装し、商用 LLM ベースの研究水準には及ばないが、相対変化の検知レベルで定量化を試みる。

---

## 品質特性のトレードオフ — 理論的背景

自動評価で極めて難解な障壁は、品質特性間の **相互作用と排他性 (トレードオフ)** である [6]。本プロジェクトの AI エージェントは、これらの依存関係を予測し、プロジェクトコンテキストに適した最適バランスを検証するアルゴリズムを保持する [6]。

理論的に重要なトレードオフ構造 (実装マッピングは [docs/quality-model.md#トレードオフ評価](quality-model.md)):

- **セキュリティ:耐性 ↑ ⇄ 性能:時間特性 ↓** — mTLS / ゼロトラスト通信 [9] は暗号化処理に伴う CPU 負荷と通信フライト時間の増大を招く [4]
- **柔軟性:スケーラビリティ ↑ ⇄ 保守性:解析性 ↓** — マイクロサービス分散 [12] はデバッグ・リフレクション解析を著しく困難にする [6]
- **保守性:モジュール性 ↑ ⇄ 性能:資源利用性 ↓** — 細粒度カプセル化は試験性を向上するが [4]、呼び出しスタック深化と IPC オーバヘッドを生む [4]

### 生成 AI 統合と「不可視の監視作業 (Invisible Oversight Labor)」

GitHub Copilot 等の生成 AI を SDLC に組み込んだ際、追加的に発生する **検証・デバッグ作業 (Invisible Oversight Labor)** が、エンジニアの認知負荷・ストレス・燃え尽き症候群を助長することが研究により示されている [34]。

短期的生産性は向上しても、長期的にはソフトウェア全体の信頼 (Acceptability:Trust) を損なう可能性がある [23][34]。本プロジェクトのトレードオフ評価には「コード追加速度 vs その後の revert / hotfix 頻度」の相関を組み込む。

---

## 本プロジェクトでの規格適用方針

本ドキュメントは規格と理論的背景のサマリにとどめる。実装層へのマッピング・スコアリング・運用判断は、以下のドキュメントに分かれて記述されている:

| 関連ドキュメント | 内容 |
| :--- | :--- |
| [docs/quality-model.md](quality-model.md) | 9 特性 + 3 特性 → OSS ツール対応, スコアモデル, トレードオフ実装 |
| [docs/architecture.md](architecture.md) | 評価エージェントを含むシステム全体構成 |
| [docs/backup.md](backup.md) | 評価結果と運用データの永続化戦略 |

---

## Works Cited

(原典 (旧 temp.md) からそのまま引用。リンク切れの追跡は将来の課題とする)

1. optimizing software quality: lessons from agile and devops practices — FDI FLOWS AND HOST COUNTRY ECONOMIC DEVELOPMENT — https://www.upet.ro/annals/economics/pdf/2024/p2/9).%20Nicolaescu.pdf
2. optimizing software quality: lessons from agile and devops practices — ResearchGate — https://www.researchgate.net/publication/390693162_OPTIMIZING_SOFTWARE_QUALITY_LESSONS_FROM_AGILE_AND_DEVOPS_PRACTICES
3. Characteristics and Sub Characteristics of ISO 25010:2023 — ResearchGate — https://www.researchgate.net/figure/Characteristics-and-Sub-Characteristics-of-ISO-250102023-Source-https-iso25000com_fig1_387960482
4. What defines Software Quality? Standards, features, and ISO/IEC 25010 — noname.solutions — https://www.noname-solutions.com/en/software-quality-iso-iec-25010/
5. ISO/IEC 25010:2023 – Systems and Software Engineering: SQuaRE – Product Quality Model — Pacific Certifications — https://pacificcert.com/iso-iec-25010-systems-and-software-engineering/
6. Software Quality Attributes in Requirements Engineering — ResearchGate — https://www.researchgate.net/publication/394404279_Software_Quality_Attributes_in_Requirements_Engineering
7. ISO/IEC 25019 — 2023-11 — DIN Media — https://www.dinmedia.de/en/standard/iso-iec-25019/375358503
8. How ISO 25010 Frameworks Reduce Technical Debt in Long-Term Projects — Monterail blog — https://www.monterail.com/blog/software-qa-standards-iso-25010
9. Systems and software engineering — SQuaRE — Measurement of product quality — ΕΛΟΤ — https://standardsdevelopment.elot.gr/drafts/14462
10. ISO/IEC 25002:2024 — Systems and software engineering — ANSI Webstore — https://webstore.ansi.org/standards/iso/isoiec250022024
11. International Standard — VDE Verlag — https://www.vde-verlag.de/iec-standards/corr-e/252678/
12. Comparative Analysis of ISO/IEC 25010:2011 and ISO/IEC 25010:2023 — Prezi — https://prezi.com/p/n363vlsvrjrk/comparative-analysis-of-isoiec-250102011-and-isoiec-250102023/
13. Shortcomings of ISO 25010 — INNOQ — https://www.innoq.com/en/articles/2023/02/iso-25010-shortcomings/
14. Ultimate Guide to Non-Functional Requirements for Architects — workingsoftware.dev — https://www.workingsoftware.dev/the-ultimate-guide-to-write-non-functional-requirements/
15. ISO 25010 Software Product Quality Model — Pacific Certifications Blog — https://blog.pacificcert.com/iso-25010-software-product-quality-model/
16. Interaction capability — arc42 Quality Model — https://quality.arc42.org/qualities/interaction-capability
17. Quality Model for Machine Learning Components — arXiv — https://arxiv.org/pdf/2602.05043
18. Software Bill of Materials in Software Supply Chain Security: A Systematic Literature Review — arXiv — https://arxiv.org/html/2506.03507v1
19. ISO/IEC 25019 — 2023 — European Defence Agency EDSTAR — https://edstar.eda.europa.eu/Standards/Details/d7f905a3-f302-4061-8c98-0146f113ad15
20. Quality Assessment of Artificial Intelligence Systems: A Metric-Based Approach — MDPI — https://www.mdpi.com/2079-9292/15/3/691
21. ISO/IEC 25019:2023 — iTeh Standards — https://cdn.standards.iteh.ai/samples/78177/7c8fe3ed9fdc4c06a8c9a14a8cdae2ec/ISO-IEC-25019-2023.pdf
22. An Exploration of the ISO/IEC 25010 Software Quality Model — Codacy Blog — https://blog.codacy.com/iso-25010-software-quality-model
23. Quality in Use in Connected Mental Health: Protocol for a Systematic Mapping Study — PMC — https://pmc.ncbi.nlm.nih.gov/articles/PMC12869151/
24. Mining User Forums to Evaluate Quality-in-Use of Environmental Software — IEEE Xplore — https://ieeexplore.ieee.org/document/11476204/
25. Quality in Use in Connected Mental Health: Protocol for a Systematic Mapping Study — ResearchGate — https://www.researchgate.net/publication/399932716_Quality_in_Use_in_Connected_Mental_Health_Protocol_for_a_Systematic_Mapping_Study
26. ISO/IEC 25019:2023 — ANSI Webstore — https://webstore.ansi.org/standards/iso/isoiec250192023
27. ISO/IEC 25019:2023 — IEC Webstore — https://webstore.iec.ch/en/publication/90105
28. Research and Development of Test Automation Maturity Model — MDPI — https://www.mdpi.com/2674-113X/4/3/19
29. BS ISO/IEC 25019:2023 — European Standards — https://www.en-standard.eu/bs-iso-iec-25019-2023-systems-and-software-engineering-systems-and-software-quality-requirements-and-evaluation-square-quality-in-use-model/
30. IEEE Computer Society — Software and Systems Engineering Vocabulary — https://pascal.computer.org/sev_display/printCatalog.action
31. Search Results — IEEE Computer Society — https://pascal.computer.org/sev_display/search.action
32. Human-Centered Redesign as a Strategy for Value Creation in Educational Information Systems — https://sol.sbc.org.br/index.php/sbsi_estendido/article/download/42035/41804
33. Software Quality Attributes in Requirements Engineering — MECS Press — https://www.mecs-press.org/ijitcs/ijitcs-v17-n4/IJITCS-V17-N4-4.pdf
34. At What Cost? Software Developers' Well-Being in the Age of GenAI — arXiv — https://arxiv.org/html/2605.22349v1
