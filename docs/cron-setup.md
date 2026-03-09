# Render Cron Job — GSC Sync Settimanale

## Configurazione in Render Dashboard

1. Vai su Render Dashboard → New → Cron Job
2. Collega lo stesso repo del backend
3. Configura:
   - **Name**: gsc-sync-weekly
   - **Schedule**: `0 6 * * 1` (ogni lunedì alle 06:00 UTC)
   - **Command**: `python cron/gsc_sync_all.py`
   - **Environment**: stesse env vars del web service principale
     (SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, GOOGLE_SERVICE_ACCOUNT_JSON)

## Note

- Il cron gira direttamente sul container — nessun cold start
- Se un cliente fallisce, il sync continua per gli altri
- I log sono visibili in Render Dashboard → Cron Job → Logs
- Schedule spiegato: 0=minuto, 6=ora, *=ogni giorno, *=ogni mese, 1=lunedì
