-- quality-agent のスコア時系列スキーマ (pg-main の quality DB)。
-- エージェントが起動時に冪等適用する (CREATE ... IF NOT EXISTS)。
-- Grafana 連携しやすい long 形式: 1 実行 = quality_runs 1 行 +
-- 特性ごとに quality_characteristic_scores 1 行。

CREATE TABLE IF NOT EXISTS quality_runs (
    run_id     BIGSERIAL,
    scored_at  TIMESTAMPTZ  NOT NULL,
    target     TEXT         NOT NULL,
    context_id TEXT         NOT NULL,
    overall    NUMERIC(5,1) NOT NULL,
    PRIMARY KEY (run_id)
);

CREATE INDEX IF NOT EXISTS idx_quality_runs_scored_at ON quality_runs (scored_at);

CREATE INDEX IF NOT EXISTS idx_quality_runs_target ON quality_runs (target);

CREATE TABLE IF NOT EXISTS quality_characteristic_scores (
    run_id            BIGINT       NOT NULL REFERENCES quality_runs (run_id) ON DELETE CASCADE,
    characteristic    TEXT         NOT NULL,
    score             NUMERIC(5,1) NOT NULL,
    weight            NUMERIC(4,2) NOT NULL,
    insufficient_data BOOLEAN      NOT NULL,
    PRIMARY KEY (run_id, characteristic)
);

-- Grafana datasource 用の読み取り専用ロール quality_ro (Phase 5) への GRANT。
-- 実行者の quality は quality DB の owner なので GRANT / ALTER DEFAULT PRIVILEGES
-- を行える。ロールは CNPG managed.roles (manifests/postgres/cluster.yaml) が
-- 作るため、未作成のクラスタでも落ちないよう pg_roles の存在確認でガードする。
-- ALTER DEFAULT PRIVILEGES により将来追加されるテーブルにも SELECT が自動付与される。
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'quality_ro') THEN
        GRANT USAGE ON SCHEMA public TO quality_ro;
        GRANT SELECT ON ALL TABLES IN SCHEMA public TO quality_ro;
        ALTER DEFAULT PRIVILEGES FOR ROLE quality IN SCHEMA public
            GRANT SELECT ON TABLES TO quality_ro;
    END IF;
END
$$;
