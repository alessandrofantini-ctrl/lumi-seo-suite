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
- BeautifulSoup4 per scraping pagine competitor

## Struttura del progetto

```
routers/          → HTTP endpoints organizzati per dominio
  clients.py      → clienti, keyword_history, GSC sync
  seo.py          → analisi SERP + brief generation
  writer.py       → generazione articoli da brief
services/         → business logic pura (no FastAPI, no Supabase — testabili in isolamento)
  openai_service.py  → prompt engineering + chiamate GPT-4o
  scraper.py         → scraping pagine web + tokenization + SERP snapshot
  serp.py            → query SerpAPI
  gsc.py             → fetch Google Search Console ultimi 28 giorni
migrations/       → SQL da applicare manualmente in Supabase Dashboard → SQL Editor
  001_keyword_status.sql
  002_gsc_integration.sql
  003_keyword_enhancements.sql
docs/adr/         → Architecture Decision Records
tests/            → test automatici (pytest)
```

## Database — Supabase

### Tabelle principali
- `clients`: profilo cliente (brand, tone_of_voice, usp, products_services, target_audience, geo, gsc_property)
- `keyword_history`: keyword target con pipeline status + GSC metrics + cluster/intent/priority
- `briefs`: brief SEO generati + eventuale articolo scritto

### Status pipeline keyword_history (ordinato)
```
backlog → planned → brief_done → written → published
```

### Campi keyword_history
```
keyword, status, cluster, intent, priority
impressions, clicks, position, ctr, gsc_updated_at
```

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

## Come aggiungere un endpoint

1. Aggiungi la route in `routers/<area>.py`
2. Proteggi con `Depends(get_current_user)` per autenticazione JWT
3. Per OpenAI/SerpAPI usa `x_openai_key: str = Header(None)` (header auto-iniettato dal FE)
4. Request body → Pydantic model (sempre)
5. I service sono funzioni pure — importale, non reinventarle

## Convenzioni di codice

- Services = funzioni pure (no import FastAPI, no Supabase diretto) → facilitano i test
- Sezioni logiche nei router delimitate con `# ══════════════`
- Migrazioni numerate `NNN_descrizione.sql`
- Response bodies: dict Python (FastAPI li serializza automaticamente)

## Workflow "prompt-safe"

Ad ogni sessione, se viene modificata una decisione architetturale:
1. Aggiorna o crea il file ADR corrispondente in `docs/adr/`
2. Se cambia logica non banale, aggiungi/aggiorna il test in `tests/`
3. Aggiorna questo file se cambiano stack, struttura o pattern
