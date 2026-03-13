CREATE TABLE IF NOT EXISTS seo_jobs (
  id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at  TIMESTAMPTZ DEFAULT now(),
  updated_at  TIMESTAMPTZ DEFAULT now(),
  user_id     UUID        REFERENCES auth.users(id),
  client_id   UUID        REFERENCES clients(id),
  keyword     TEXT        NOT NULL,
  market      TEXT,
  intent      TEXT,
  status      TEXT        NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','running','done','error')),
  result      JSONB,
  error       TEXT
);

CREATE INDEX IF NOT EXISTS idx_seo_jobs_user_id ON seo_jobs(user_id);
CREATE INDEX IF NOT EXISTS idx_seo_jobs_status  ON seo_jobs(status);
