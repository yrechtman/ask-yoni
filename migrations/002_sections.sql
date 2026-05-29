-- Sections: a Substack post often packages multiple distinct topics
-- separated by horizontal rules. We index at the section level so search
-- returns the right idea, not the whole issue.

CREATE TABLE IF NOT EXISTS substack_sections (
    id          BIGSERIAL PRIMARY KEY,
    post_id     TEXT      NOT NULL REFERENCES substack_posts(post_id) ON DELETE CASCADE,
    section_idx INTEGER   NOT NULL,
    title       TEXT      NOT NULL,
    body_md     TEXT      NOT NULL,
    word_count  INTEGER   NOT NULL,
    kind        TEXT      NOT NULL CHECK (kind IN ('main', 'meta')),
    summary     TEXT,
    anchor      TEXT,
    UNIQUE (post_id, section_idx)
);

CREATE INDEX IF NOT EXISTS substack_sections_post_id_idx
    ON substack_sections (post_id);
CREATE INDEX IF NOT EXISTS substack_sections_kind_idx
    ON substack_sections (kind);

-- Chunks now reference sections, not posts directly. The original index +
-- column on post_id are no longer useful; we rebuild the table.
DROP INDEX IF EXISTS substack_chunks_embedding_idx;
DROP INDEX IF EXISTS substack_chunks_post_id_idx;
DROP TABLE IF EXISTS substack_chunks CASCADE;

CREATE TABLE substack_chunks (
    id          BIGSERIAL PRIMARY KEY,
    section_id  BIGINT    NOT NULL REFERENCES substack_sections(id) ON DELETE CASCADE,
    chunk_idx   INTEGER   NOT NULL,
    text        TEXT      NOT NULL,
    token_count INTEGER   NOT NULL,
    embedding   vector(1536) NOT NULL,
    UNIQUE (section_id, chunk_idx)
);

CREATE INDEX IF NOT EXISTS substack_chunks_section_id_idx
    ON substack_chunks (section_id);

CREATE INDEX IF NOT EXISTS substack_chunks_embedding_idx
    ON substack_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
