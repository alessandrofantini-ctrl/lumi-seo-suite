-- Tabella storico snapshot posizioni ad ogni GSC sync
CREATE TABLE IF NOT EXISTS keyword_position_history (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  keyword_id      UUID NOT NULL REFERENCES keyword_history(id) ON DELETE CASCADE,
  client_id       UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  position        FLOAT NOT NULL,
  clicks          INT,
  impressions     INT,
  ctr             FLOAT,
  recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_kph_keyword_id  ON keyword_position_history(keyword_id);
CREATE INDEX IF NOT EXISTS idx_kph_client_id   ON keyword_position_history(client_id);
CREATE INDEX IF NOT EXISTS idx_kph_recorded_at ON keyword_position_history(recorded_at);
