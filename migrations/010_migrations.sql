CREATE TABLE IF NOT EXISTS migrations (
  id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at  TIMESTAMPTZ DEFAULT now(),
  name        TEXT,
  old_domain  TEXT,
  new_domains JSONB,
  results     JSONB,
  total_urls  INT,
  matched_urls INT
);
