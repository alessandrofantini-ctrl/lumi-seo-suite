-- Migrazione 004: aggiunge position_prev e position_updated_at a keyword_history
-- Applica manualmente in Supabase Dashboard → SQL Editor

ALTER TABLE keyword_history ADD COLUMN IF NOT EXISTS position_prev FLOAT;
ALTER TABLE keyword_history ADD COLUMN IF NOT EXISTS position_updated_at TIMESTAMPTZ;
