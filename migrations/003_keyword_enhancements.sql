-- Keyword enhancements: cluster, intent, priority per organizzare le query target

ALTER TABLE keyword_history
  ADD COLUMN IF NOT EXISTS cluster  TEXT,
  ADD COLUMN IF NOT EXISTS intent   TEXT CHECK (intent IN ('informativo', 'commerciale', 'navigazionale', 'transazionale')),
  ADD COLUMN IF NOT EXISTS priority TEXT DEFAULT 'media' CHECK (priority IN ('alta', 'media', 'bassa'));

-- Indice su cluster per velocizzare i raggruppamenti
CREATE INDEX IF NOT EXISTS idx_keyword_history_cluster ON keyword_history (client_id, cluster);
