-- Migration 005: DataForSEO search volume integration
-- Applica manualmente in Supabase Dashboard → SQL Editor

ALTER TABLE keyword_history ADD COLUMN IF NOT EXISTS search_volume INT;
ALTER TABLE keyword_history ADD COLUMN IF NOT EXISTS volume_updated_at TIMESTAMPTZ;

ALTER TABLE clients ADD COLUMN IF NOT EXISTS language_code TEXT DEFAULT 'it';
ALTER TABLE clients ADD COLUMN IF NOT EXISTS location_code INT DEFAULT 2380;
