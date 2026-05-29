-- Drop the tags column added in 003. Semantic search via embeddings makes
-- tags redundant for primary lookup; the experiment showed LLM-extracted
-- tags carry too much recency bias to be useful for browsing either.
-- substack_sync_runs is kept (it's useful for health visibility).

DROP INDEX IF EXISTS substack_sections_tags_gin;
ALTER TABLE substack_sections DROP COLUMN IF EXISTS tags;
