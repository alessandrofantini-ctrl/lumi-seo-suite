# Setup sistema multi-utente

## Prerequisiti

1. Applicare la migration SQL `migrations/011_multiuser.sql` in Supabase Dashboard → SQL Editor
2. Avere le variabili d'ambiente `SUPABASE_URL` e `SUPABASE_SERVICE_ROLE_KEY` configurate

## Creazione primo admin

Usa lo script `scripts/create_first_admin.py`:

```bash
cd /path/to/lumi-seo-suite

SUPABASE_URL=https://xxx.supabase.co \
SUPABASE_SERVICE_ROLE_KEY=eyJ... \
ADMIN_EMAIL=admin@example.com \
ADMIN_PASSWORD=password_sicura \
ADMIN_NAME="Alessandro Fantini" \
python scripts/create_first_admin.py
```

## Ruoli

| Ruolo | Accesso |
|-------|---------|
| `admin` | Vede tutti i clienti; può creare/eliminare utenti; può assegnare clienti |
| `specialist` | Vede solo clienti di cui è `owner_id` o `assigned_to` |

## Flusso operativo

1. L'admin accede alla pagina `/admin` dalla sidebar
2. Nella tab **Utenti**: crea nuovi account specialist con email + password
3. Nella tab **Assegnazioni**: assegna clienti agli specialist

## API Admin (riservate al ruolo admin)

| Metodo | Path | Descrizione |
|--------|------|-------------|
| `GET`  | `/api/admin/users` | Lista tutti gli utenti |
| `POST` | `/api/admin/users` | Crea nuovo utente |
| `PATCH`| `/api/admin/users/{id}` | Aggiorna ruolo/nome |
| `DELETE`| `/api/admin/users/{id}` | Elimina utente |
| `GET`  | `/api/admin/clients` | Lista clienti con owner/assigned |
| `PATCH`| `/api/admin/clients/{id}/assign` | Assegna cliente a specialist |

## API Auth

| Metodo | Path | Descrizione |
|--------|------|-------------|
| `GET`  | `/api/auth/me` | Profilo utente corrente (id, email, role, full_name) |

## Campi aggiunti a `clients`

- `owner_id` (UUID): utente che ha creato il cliente (auto-impostato alla creazione)
- `assigned_to` (UUID): specialist assegnato dall'admin (opzionale)

## Note di sicurezza

- Le API admin usano `require_admin` che verifica `user_profiles.role == "admin"`
- Se un utente non ha un profilo in `user_profiles`, il sistema lo tratta come `specialist` di default
- Per revocare l'accesso a un utente: elimina il suo profilo o cambia il ruolo a `specialist` e rimuovi le assegnazioni
