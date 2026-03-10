-- Migration 007: aggiungi volume_refreshed_at ai clienti per throttle DataForSEO
ALTER TABLE clients
  ADD COLUMN IF NOT EXISTS volume_refreshed_at TIMESTAMPTZ;
