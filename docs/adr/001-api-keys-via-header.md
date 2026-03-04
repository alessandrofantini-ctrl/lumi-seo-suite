# ADR-001: API keys OpenAI e SerpAPI trasmesse via header HTTP

**Data:** 2024 (implementato, retroattivamente documentato 2026-03-04)
**Stato:** Approvato

---

## Contesto

Il backend ha bisogno di chiamare OpenAI GPT-4o (per brief e articoli) e SerpAPI (per le SERP).
Queste API sono a pagamento e richiedono una chiave personale.

## Problema

Come gestire le chiavi API senza:
- Hardcodarle nel codice
- Condividerle tra tutti gli utenti se il tool venisse espanso
- Richiedere un restart del server ad ogni cambio di chiave

## Opzioni valutate

| Opzione | Pro | Contro |
|---------|-----|--------|
| **Env vars sul server** | Standard, sicuro lato server | Richiede accesso Render per cambiarle; una sola chiave per tutti |
| **Header HTTP dal client** | Flessibile, ogni utente usa la propria chiave | Chiavi nel browser (localStorage) |
| **Database per utente** | Multi-tenant vero | Over-engineering per tool mono-utente |

## Decisione

**Header HTTP dal client**, con chiavi salvate in `localStorage` del browser.

Il frontend (`lib/api.ts`) inietta automaticamente:
- `X-OpenAI-Key` → `localStorage.getItem("lumi_openai_key")`
- `X-SerpAPI-Key` → `localStorage.getItem("lumi_serpapi_key")`

Il backend le legge come FastAPI `Header`:
```python
x_openai_key: str = Header(None)
x_serpapi_key: str = Header(None)
```

## Razionale

Il tool è **mono-utente** (Alessandro, HEAD of SEO di Lumi Company).
Il rischio di esporre le chiavi nel localStorage è accettabile per un tool interno
usato su macchina personale con sessione autenticata Supabase.

La flessibilità di cambiare chiave direttamente dall'UI (`/impostazioni`)
senza toccare variabili d'ambiente Render è un vantaggio operativo reale.

## Conseguenze

- Le chiavi vivono in `localStorage` — non sopravvivono a clear della cache
- Se il tool diventasse multi-tenant vero, questa architettura va rivista
- La pagina `/impostazioni` è il punto unico di gestione delle chiavi
- GSC usa ancora service account JSON da env (installazione unica, non per utente)
