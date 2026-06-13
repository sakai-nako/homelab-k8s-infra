"""ツール出力を Metric に正規化する collector 群.

各 collector は副作用なく Metric のリストを返す。Walking skeleton では trivy
のみ。今後 sonarqube / forgejo メタデータ / kube-linter などを追加する。
"""
