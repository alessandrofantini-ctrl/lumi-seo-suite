ALTER TABLE keyword_history
  ADD COLUMN IF NOT EXISTS published_url TEXT;
ALTER TABLE keyword_history
  ADD COLUMN IF NOT EXISTS page_position FLOAT;
ALTER TABLE keyword_history
  ADD COLUMN IF NOT EXISTS page_clicks INT;
ALTER TABLE keyword_history
  ADD COLUMN IF NOT EXISTS page_impressions INT;
ALTER TABLE keyword_history
  ADD COLUMN IF NOT EXISTS page_ctr FLOAT;
ALTER TABLE keyword_history
  ADD COLUMN IF NOT EXISTS page_updated_at TIMESTAMPTZ;
