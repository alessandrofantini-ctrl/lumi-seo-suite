# Lumi SEO Suite — Backend

## Cos'è
Tool interno per HEAD of SEO (Lumi Company). Gestisce clienti SEO, keyword pipeline,
analisi SERP+competitor scraping, brief generation e article writing tramite GPT-4o.

Utente primario: un singolo SEO specialist (Alessandro). Non è un SaaS multi-tenant.

## Stack
- FastAPI 0.111 + Python 3.11 — deploy su Render (Procfile)
- Supabase: PostgreSQL + Auth JWT
- OpenAI GPT-4o — chiave via header HTTP, NON da env (vedi `docs/adr/001-api-keys-via-header.md`)
- SerpAPI — chiave via header HTTP, NON da env (vedi `docs/adr/001-api-keys-via-header.md`)
- Google Search Console API — service account JSON da env (installazione unica)
- DataForSEO — credenziali via env vars lato server (DATAFORSEO_LOGIN, DATAFORSEO_PASSWORD)
- BeautifulSoup4 per scraping pagine competitor

## Struttura del progetto

```
routers/          → HTTP endpoints organizzati per dominio
  clients.py      → clienti, keyword_history, GSC sync, DataForSEO volume enrichment
  seo.py          → analisi SERP + brief generation
  writer.py       → generazione articoli da brief
  migration.py    → mapping redirect 301: analisi CSV Screaming Frog + GPT-4o + export CSV
  dashboard.py    → vista cross-cliente: usato da app/clients/page.tsx come sorgente dati principale
services/         → business logic pura (no FastAPI, no Supabase — testabili in isolamento)
  openai_service.py  → prompt engineering + chiamate GPT-4o
  scraper.py         → scraping pagine web + tokenization + SERP snapshot
  serp.py            → query SerpAPI
  gsc.py             → fetch Google Search Console ultimi 28 giorni
                        fetch_gsc_queries(site_url, days) → metriche per keyword (query)
                        fetch_gsc_page_metrics(property_url, page_url) → metriche per URL pagina
  dataforseo.py      → get_search_volume() — volume mensile da DataForSEO Google Ads API
cron/             → script standalone per Render Cron Job (nessun import FastAPI)
  gsc_sync_all.py → sync GSC settimanale per tutti i clienti con gsc_property configurata
migrations/       → SQL da applicare manualmente in Supabase Dashboard → SQL Editor
  001_keyword_status.sql
  002_gsc_integration.sql
  003_keyword_enhancements.sql
  004_position_prev.sql
  005_dataforseo.sql
  006_position_history.sql
docs/adr/         → Architecture Decision Records
docs/cron-setup.md → istruzioni configurazione Render Cron Job per GSC sync settimanale
tests/            → test automatici (pytest)
```

## Database — Supabase

### Tabelle principali
- `clients`: profilo cliente (brand, tone_of_voice, usp, products_services, target_audience, geo, gsc_property, language_code, location_code)
- `keyword_history`: keyword target con pipeline status + GSC metrics + cluster/intent/priority
- `briefs`: brief SEO generati + eventuale articolo scritto
- `keyword_position_history`: snapshot posizioni ad ogni GSC sync (migration 006)
  - `keyword_id` → FK keyword_history | `client_id` → FK clients | `position`, `clicks`, `impressions`, `ctr`, `recorded_at`
  - Inserito automaticamente da `gsc_sync` ad ogni sync riuscito
  - Endpoint: `GET /api/clients/{id}/keywords/{kw_id}/history` (90gg, ordinato asc)
  - Endpoint: `GET /api/clients/{id}/visibility-history` (posizione media ponderata per giorno, 90gg)

### Status pipeline keyword_history (ordinato)
```
backlog → planned → brief_done → written → published
```

### Campi keyword_history
```
keyword, status, cluster, intent, priority
impressions, clicks, position, ctr, gsc_updated_at
position_prev, position_updated_at   ← aggiunto in migration 004
search_volume, volume_updated_at     ← aggiunto in migration 005 (DataForSEO)
published_url                        ← aggiunto in migration 008 (URL pagina pubblicata)
page_position, page_clicks, page_impressions, page_ctr, page_updated_at
                                     ← aggiunto in migration 008 (rendimento GSC della pagina)
planned_month                        ← aggiunto in migration 009 (TEXT, formato "YYYY-MM")
```
Nota: `impressions/clicks/position/ctr` = rendimento della keyword come query di ricerca.
`page_*` = rendimento della pagina pubblicata come URL (aggregate su tutte le query che portano a quell'URL).

### Valori intent validi
`informativo | commerciale | navigazionale | transazionale`

### Valori priority validi
`alta | media | bassa`

### Come aggiungere migrazioni
Creare file `migrations/NNN_descrizione.sql` e applicarlo manualmente in Supabase Dashboard.

## Decisioni architetturali — NON modificare senza aggiornare l'ADR corrispondente

| # | Decisione | ADR |
|---|-----------|-----|
| 1 | API keys OpenAI/SerpAPI via header HTTP, non env vars | `docs/adr/001-api-keys-via-header.md` |
| 2 | GSC sync aggiorna SOLO keyword esistenti, non importa nuove query | `docs/adr/002-gsc-sync-update-only.md` |
| 3 | JWT verificato con Supabase SDK (non decode HS256 manuale) | commit `9d433cc` |
| 4 | CORS `allow_origins=["*"]` — da restringere all'URL Vercel in produzione | `main.py:14` |
| 5 | GSC sync salva `position_prev` prima di sovrascrivere `position` | `routers/clients.py` — gsc_sync |
| 6 | GSC sync inserisce snapshot in `keyword_position_history` (trend storico) | `routers/clients.py` — gsc_sync |

## Endpoint bulk keyword (routers/clients.py)

### POST `/{client_id}/keywords/bulk`
- Body: `{ keywords: KeywordItem[] }` dove `KeywordItem = { keyword, cluster?, intent?, priority? }`
- Salta duplicati (case-insensitive su `existing_set`)
- Valida `intent` contro `VALID_INTENT = {informativo, commerciale, navigazionale, transazionale}`
- Valida `priority` contro `VALID_PRIORITY = {alta, media, bassa}` — valori non validi ignorati
- Salva `cluster`, `intent`, `priority` se presenti nella riga CSV
- Dopo insert: chiama DataForSEO in batch per arricchire `search_volume`
- Response: `{ added: N, skipped: M }`

## Endpoint volume refresh (routers/clients.py)

### POST `/{client_id}/keywords/refresh-volumes`
- Protetto con `Depends(get_current_user)`
- Nessun body
- Throttle 30 giorni: controlla `clients.volume_refreshed_at`; se < 30gg fa → risponde `{ skipped: true, reason, next_refresh }`
- Altrimenti: carica tutte le keyword del cliente, chiama `get_search_volume` in batch, aggiorna `search_volume` + `volume_updated_at` per ogni keyword, aggiorna `clients.volume_refreshed_at = now()`
- Response: `{ skipped: false, updated: N, cost_estimate: "~$X.XXXX" }`
- Richiede `DATAFORSEO_LOGIN` + `DATAFORSEO_PASSWORD` env vars (503 se assenti)
- Nota: POST `/{client_id}/keywords` (singola) NON chiama più DataForSEO — solo bulk e refresh manuale
- Migration: `migrations/007_volume_refresh.sql` — aggiunge `volume_refreshed_at TIMESTAMPTZ` a `clients`

## Endpoint calendario (routers/clients.py)

### GET `/calendar`
- Protetto con `Depends(get_current_user)`
- Nessun body
- Restituisce tutte le keyword con `planned_month` non null/vuoto, con join `clients(id, name)`
- Select: `id, keyword, status, planned_month, client_id, cluster, intent, priority, clients(id, name)`
- IMPORTANTE: questa route è definita PRIMA di `/{client_id}` per evitare conflitti FastAPI

## Endpoint GET /api/clients (routers/clients.py)

### GET `/api/clients`
- Protetto con `Depends(get_current_user)`
- Aggrega dati da `keyword_position_history` (ultimi 28gg e 28-56gg fa) e `keyword_history`
- Campi aggiuntivi per ogni cliente rispetto ai dati base della tabella `clients`:
  - `total_keywords`, `keywords_crescita`, `keywords_calo`, `last_sync` — trend keyword (posizione vs position_prev)
  - `clicks_curr`, `impressions_curr`, `avg_position` — metriche GSC aggregate ultimi 28gg da `keyword_position_history`
  - `clicks_trend`, `impressions_trend` — percentuale variazione vs mese precedente (28-56gg fa); `null` se mese precedente = 0
- Response: `[{ ...client_fields, total_keywords, keywords_crescita, keywords_calo, last_sync, clicks_curr, impressions_curr, avg_position, clicks_trend, impressions_trend }]`

## Endpoint GET /api/clients/{id}/summary (routers/clients.py)

### GET `/api/clients/{client_id}/summary`
- Protetto con `Depends(get_current_user)`
- Aggrega metriche GSC da `keyword_history` (campi `clicks`, `impressions`, `ctr`, `position`)
- Response:
  - `total_clicks`, `total_impressions` — somma di tutti i click/impressioni keyword del cliente
  - `avg_position` — media posizione (arrotondata a 1 decimale); `null` se nessuna keyword con posizione
  - `avg_ctr` — CTR medio in % (arrotondato a 1 decimale); `null` se nessuna keyword
  - `top_clicks` — top 5 keyword ordinate per click desc
  - `top_impressions` — top 5 keyword ordinate per impressioni desc

## Endpoint dashboard (routers/dashboard.py)

### GET `/api/dashboard`
- Protetto con `Depends(get_current_user)`
- Nessun body — risponde con array JSON
- **Usato come sorgente dati principale da `app/clients/page.tsx`** (lista clienti + KPI globali)
- Legge tutti i clienti (`clients`) e tutte le keyword (`keyword_history.client_id, position, position_prev, gsc_updated_at`)
- Per ogni cliente calcola:
  - `total_keywords`: conteggio righe keyword_history
  - `keywords_crescita`: righe con `position != null AND position_prev != null AND position < position_prev`
  - `keywords_calo`: righe con `position != null AND position_prev != null AND position > position_prev`
  - `last_sync`: valore massimo di `gsc_updated_at` tra le keyword del cliente
- Ordine risposta: `keywords_calo` desc (clienti più critici prima)
- Response: `[{ id, name, sector, total_keywords, keywords_crescita, keywords_calo, last_sync }]`
- Nota: `tone_of_voice` non è incluso nella response (il frontend lo mostra se presente, ma non è restituito da questo endpoint)

## Endpoint migrazione (routers/migration.py)

### POST `/api/migration/analyze`
- Protetto con `Depends(get_current_user)` + header `X-OpenAI-Key`
- Usa `request: Request` per leggere form fields dinamici
- Multipart form-data:
  - `config`: JSON string con `old_domain`, `new_domains: [{id, domain, label}]`, `language_rules: [{pattern, pattern_type, target_domain_id, behavior, consolidated_target_domain_id?}]`
  - `old_csv`: CSV sito vecchio (unico, sempre presente)
  - `new_csv_{domain_id}`: CSV per ogni new domain (field name usa l'id UUID del dominio)
- Filtra righe `Content Type` contiene `text/html` AND `Status Code == 200`
- Core matching (`_match_pages`): 3 livelli su `match_slug` — esatto (100%), overlap token (≥80%→85%, ≥60%→65%, ≥40%→40%), GPT-4o batch
- `match_slug` = slug normalizzato senza prefisso lingua (es. `/it/guida-seo` → `/guida-seo`)
- Logica instradamento:
  - Se nessuna `language_rule`: tutte le old pages matchate contro pool combinato di tutti i new CSV
  - Se regole presenti: ogni old page assegnata alla prima regola corrispondente (`_url_matches_rule`):
    - `behavior=redirect`: matching standard contro new_csv del dominio target
    - `behavior=eliminated`: match_type="eliminated", no redirect
    - `behavior=consolidated`: matching contro new_csv del dominio di consolidamento, match_type="consolidated"
  - Old pages senza regola corrispondente: fallback su pool combinato di tutti i new CSV
- `url_to_domain_id` dict inverso: `{new_url → domain_id}` per annotare `target_domain`/`target_label` post-match
- Risposta: `{ total, matched, no_match, eliminated, results: [...], stats: { exact, slug, gpt, no_match, eliminated, consolidated } }`
- `MigrationResult` fields: `target_domain` (URL del dominio dest), `target_label` (label opzionale); match_type include "eliminated"|"consolidated"

### POST `/api/migration/export-csv`
- Protetto con `Depends(get_current_user)`
- Body JSON: `{ results: [...], old_domain: "..." }`
- Usa `r.target_domain` per-risultato per la colonna "Dominio nuovo"
- Ritorna file CSV (StreamingResponse) con BOM UTF-8 per compatibilità Excel
- Header: `Content-Disposition: attachment; filename=migration_mapping.csv`
- Colonne: URL vecchio, URL nuovo, Dominio nuovo, Label dominio, Confidenza %, Tipo match, Motivo, Title vecchio, Title nuovo, H1 vecchio, Inlinks

## Come aggiungere un endpoint

1. Aggiungi la route in `routers/<area>.py`
2. Proteggi con `Depends(get_current_user)` per autenticazione JWT
3. Per OpenAI/SerpAPI usa `x_openai_key: str = Header(None)` (header auto-iniettato dal FE)
4. Request body → Pydantic model (sempre)
5. I service sono funzioni pure — importale, non reinventarle

## Variabili d'ambiente (Render)

```
SUPABASE_URL              → URL progetto Supabase
SUPABASE_SERVICE_ROLE_KEY → service role key Supabase
GOOGLE_SERVICE_ACCOUNT_JSON → JSON service account GSC (base64 o raw)
DATAFORSEO_LOGIN          → login account DataForSEO (credenziale server, non via header)
DATAFORSEO_PASSWORD       → password account DataForSEO (credenziale server, non via header)
```

## Convenzioni di codice

- Services = funzioni pure (no import FastAPI, no Supabase diretto) → facilitano i test
- Sezioni logiche nei router delimitate con `# ══════════════`
- Migrazioni numerate `NNN_descrizione.sql`
- Response bodies: dict Python (FastAPI li serializza automaticamente)

## Cron Jobs

| Script | Schedule | Comando Render | Descrizione |
|--------|----------|----------------|-------------|
| `cron/gsc_sync_all.py` | `0 6 * * 1` (lun 06:00 UTC) | `python cron/gsc_sync_all.py` | Sync GSC per tutti i clienti con `gsc_property` configurata |

- Usa `SUPABASE_SERVICE_ROLE_KEY` (service role, accesso diretto senza JWT)
- Riusa `services/gsc.py` — nessuna duplicazione della logica fetch GSC
- Se un cliente fallisce, il sync continua per gli altri (errori loggati, no exit)
- Istruzioni configurazione Render: `docs/cron-setup.md`

## Workflow "prompt-safe"

Ad ogni sessione, se viene modificata una decisione architetturale:
1. Aggiorna o crea il file ADR corrispondente in `docs/adr/`
2. Se cambia logica non banale, aggiungi/aggiorna il test in `tests/`
3. Aggiorna questo file se cambiano stack, struttura o pattern
