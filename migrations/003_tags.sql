-- Per-section tags for filtering and browsing the archive.
-- Tags are normalized kebab-case strings (e.g. 'ai-deployment', 'seed-investing').

ALTER TABLE substack_sections
    ADD COLUMN IF NOT EXISTS tags TEXT[] NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS substack_sections_tags_gin
    ON substack_sections USING gin (tags);

-- Track when the last successful sync ran so we (and future health checks)
-- can see whether the cron is alive.
CREATE TABLE IF NOT EXISTS substack_sync_runs (
    id          BIGSERIAL PRIMARY KEY,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    status      TEXT NOT NULL CHECK (status IN ('running', 'ok', 'failed')),
    new_posts   INTEGER NOT NULL DEFAULT 0,
    note        TEXT
);

CREATE INDEX IF NOT EXISTS substack_sync_runs_finished_at
    ON substack_sync_runs (finished_at DESC);
