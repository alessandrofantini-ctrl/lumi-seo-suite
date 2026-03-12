-- Tabella profili utente (estende Supabase auth.users)
CREATE TABLE IF NOT EXISTS user_profiles (
  id          UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  email       TEXT NOT NULL,
  full_name   TEXT,
  role        TEXT NOT NULL DEFAULT 'specialist'
                CHECK (role IN ('admin', 'specialist')),
  created_at  TIMESTAMPTZ DEFAULT now()
);

-- Assegnazione clienti agli specialist
-- owner_id: chi ha creato il cliente
-- assigned_to: specialist assegnato dall'admin (opzionale)
ALTER TABLE clients
  ADD COLUMN IF NOT EXISTS owner_id    UUID REFERENCES auth.users(id),
  ADD COLUMN IF NOT EXISTS assigned_to UUID REFERENCES auth.users(id);

-- Indici
CREATE INDEX IF NOT EXISTS idx_clients_owner_id    ON clients(owner_id);
CREATE INDEX IF NOT EXISTS idx_clients_assigned_to ON clients(assigned_to);
CREATE INDEX IF NOT EXISTS idx_user_profiles_role  ON user_profiles(role);
