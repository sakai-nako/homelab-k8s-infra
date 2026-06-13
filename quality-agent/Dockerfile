# quality-agent コンテナ. nightly CronJob から呼び出す。
# 本体は標準ライブラリ + PyYAML のみ依存なので slim ベースで十分に軽量。
# collector が使う外部バイナリを同梱する: kubectl (trivy) / kube-linter (lint 系)。
FROM python:3.12-slim

ARG KUBECTL_VERSION=v1.31.4
ARG KUBE_LINTER_VERSION=v0.8.3
# kube-linter-linux.tar.gz の公式リリース添付 SHA256 (供給元検証)
ARG KUBE_LINTER_SHA256=1a6d8419b11971372971fdbc22682b684ebfb7cf1c39591662d1b6ca736c41df

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL -o /usr/local/bin/kubectl \
        "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
    && chmod +x /usr/local/bin/kubectl \
    && curl -fsSL -o /tmp/kube-linter.tar.gz \
        "https://github.com/stackrox/kube-linter/releases/download/${KUBE_LINTER_VERSION}/kube-linter-linux.tar.gz" \
    && echo "${KUBE_LINTER_SHA256}  /tmp/kube-linter.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/kube-linter.tar.gz -C /usr/local/bin kube-linter \
    && rm /tmp/kube-linter.tar.gz \
    && apt-get purge -y curl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY quality_agent ./quality_agent
COPY policies ./policies
COPY contexts ./contexts

# PostgreSQL 保存 (report --to-postgres) を使うため [pg] extra も入れる。
RUN pip install --no-cache-dir ".[pg]"

ENTRYPOINT ["quality-agent"]
CMD ["--help"]
