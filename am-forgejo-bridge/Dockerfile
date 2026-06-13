# am-forgejo-bridge コンテナ。Alertmanager webhook → Forgejo Issue 起票。
# 標準ライブラリのみ依存なので slim ベース + pip install で完結する。
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml ./
COPY am_forgejo_bridge ./am_forgejo_bridge
RUN pip install --no-cache-dir .

# 書き込み先を持たない常駐プロセスなので nobody で動かす
USER 65534:65534
EXPOSE 8080

ENTRYPOINT ["python", "-m", "am_forgejo_bridge"]
