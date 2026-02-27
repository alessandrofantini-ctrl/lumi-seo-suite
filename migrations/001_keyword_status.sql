-- Migration 001: add status column to keyword_history
-- Run this in Supabase Dashboard → SQL Editor

ALTER TABLE keyword_history
  ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'backlog';

-- Optional: add a check constraint to enforce valid values
ALTER TABLE keyword_history
  ADD CONSTRAINT keyword_status_check
  CHECK (status IN ('backlog', 'planned', 'brief_done', 'written', 'published'));
