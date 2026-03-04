# ADR-002: GSC sync aggiorna solo keyword esistenti — non importa nuove query

**Data:** 2026-03-04
**Stato:** Approvato

---

## Contesto

Google Search Console restituisce tutte le query per cui un sito appare nei risultati.
Questo può essere un volume molto elevato (migliaia di query), la maggior parte delle quali
irrilevanti per la strategia SEO in corso.

Il tool gestisce una `keyword_history` con le keyword su cui l'SEO specialist sta
attivamente lavorando (pipeline: backlog → planned → brief_done → written → published).

## Problema

Il GSC sync deve portare dati reali (impressioni, click, posizione, CTR) nelle keyword.
Ma come gestire le query GSC che non sono nella lista target?

## Opzioni valutate

| Opzione | Pro | Contro |
|---------|-----|--------|
| **Importa tutto nel backlog** | Nessuna query persa | Rumore enorme, centinaia/migliaia di keyword non rilevanti |
| **Aggiorna solo keyword esistenti** | Lista rimane curata ed editoriale | Query GSC non presenti non vengono tracciate |
| **Endpoint separato per discovery** | Separazione netta | Complessità aggiuntiva non necessaria ora |

## Decisione

**Il sync GSC aggiorna SOLO le keyword già presenti in `keyword_history`.**

Se una query GSC non ha una corrispondenza (case-insensitive) in `keyword_history`,
viene ignorata. Non viene inserita nel database.

```python
# routers/clients.py — gsc_sync endpoint
for row in rows:
    query = row["query"]
    if query.lower() not in existing_map:
        continue  # ignora — non fa parte della lista target
    supabase.table("keyword_history").update({...}).eq("id", existing_map[query.lower()]).execute()
```

La response restituisce `{ synced: N, total: M }` dove:
- `synced` = keyword aggiornate con dati GSC
- `total` = keyword totali in lista (incluse quelle senza match GSC)

## Razionale

La lista keyword è **editoriale** — è l'HEAD of SEO che decide su quali query
stare lavorando. GSC è uno strumento di misurazione, non di discovery automatica.

Se `synced < total`, il gap indica keyword che Google non ha ancora indicizzato
o su cui il sito non compare — informazione utile di per sé.

## Conseguenze

- L'elenco keyword è sempre sotto controllo dell'utente
- Il GSC sync è un "enrichment" di dati, non un import
- Per aggiungere keyword scoperte via GSC, l'utente deve farlo manualmente
  (o tramite CSV import) — scelta deliberata
- Se in futuro si vuole un "discovery mode", va implementato come endpoint separato
