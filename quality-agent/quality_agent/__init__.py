"""quality-agent — ISO/IEC 25010:2023 + 25019:2023 自動品質評価エージェント.

collect -> analyze -> score -> report のパイプラインとして呼び出される。
実装済み特性: security (Trivy CVE + Macaron SLSA 監査) / maintainability
(SonarQube) / reliability・performance・safety (kube-linter) / interaction
(Ollama 文書評価) / beneficialness・acceptability (Forgejo Issue/PR メタデータ)。
詳細は docs/quality-model.md。
"""

__version__ = "0.6.0"
