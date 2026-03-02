-- GSC integration: aggiunge gsc_property ai clienti e colonne metriche alle keyword

ALTER TABLE clients ADD COLUMN IF NOT EXISTS gsc_property TEXT;

ALTER TABLE keyword_history ADD COLUMN IF NOT EXISTS impressions   INT;
ALTER TABLE keyword_history ADD COLUMN IF NOT EXISTS clicks        INT;
ALTER TABLE keyword_history ADD COLUMN IF NOT EXISTS position      FLOAT;
ALTER TABLE keyword_history ADD COLUMN IF NOT EXISTS ctr           FLOAT;
ALTER TABLE keyword_history ADD COLUMN IF NOT EXISTS gsc_updated_at TIMESTAMPTZ;
