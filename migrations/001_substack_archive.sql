CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS substack_posts (
    post_id      TEXT        PRIMARY KEY,
    slug         TEXT        NOT NULL UNIQUE,
    url          TEXT        NOT NULL,
    title        TEXT        NOT NULL,
    subtitle     TEXT,
    published_at TIMESTAMPTZ NOT NULL,
    audience     TEXT,
    body_md      TEXT        NOT NULL,
    word_count   INTEGER     NOT NULL,
    indexed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS substack_posts_published_at_idx
    ON substack_posts (published_at DESC);

CREATE TABLE IF NOT EXISTS substack_chunks (
    id          BIGSERIAL PRIMARY KEY,
    post_id     TEXT      NOT NULL REFERENCES substack_posts(post_id) ON DELETE CASCADE,
    chunk_idx   INTEGER   NOT NULL,
    text        TEXT      NOT NULL,
    token_count INTEGER   NOT NULL,
    embedding   vector(1536) NOT NULL,
    UNIQUE (post_id, chunk_idx)
);

CREATE INDEX IF NOT EXISTS substack_chunks_post_id_idx
    ON substack_chunks (post_id);

CREATE INDEX IF NOT EXISTS substack_chunks_embedding_idx
    ON substack_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
