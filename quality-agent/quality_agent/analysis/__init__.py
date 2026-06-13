"""収集メトリクスから派生所見 (Finding) を導く解析層.

ルールベースで取れるものはルールベースで取り、意味理解が要る箇所のみ将来
Ollama (ollama-external 経由) に委譲する。Walking skeleton ではルールベースのみ。
"""
