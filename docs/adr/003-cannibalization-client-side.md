# ADR-003: Rilevamento cannibalizzazione keyword eseguito lato client

**Data:** 2026-03-04
**Stato:** Approvato

---

## Contesto

Il tool deve avvisare l'SEO specialist quando due o più keyword della stessa lista
rischiano di cannibalizzarsi (competere per gli stessi posizionamenti).

## Problema

Dove eseguire l'algoritmo di rilevamento?

## Opzioni valutate

| Opzione | Pro | Contro |
|---------|-----|--------|
| **Endpoint backend dedicato** | Logica centralizzata, testabile in isolamento | Latenza di rete aggiuntiva; richiesta API per ogni modifica alla lista |
| **Calcolo client-side** | Zero latenza, aggiornamento reattivo, no API call | Logica nel componente React, duplicabile se serve lato server |

## Decisione

**Calcolo client-side**, tramite `useMemo` nel componente `app/clients/[id]/page.tsx`.

### Algoritmo (detectCannibalization)

1. Raggruppa le keyword per `intent` (solo quelle con intent assegnato)
2. Per ogni coppia nello stesso gruppo di intent:
   - Tokenizza entrambe: minuscolo → split su spazi → filtra token con `length > 2` e non in `STOP`
   - Calcola overlap tra i due set di token
   - Se `overlap.length >= 2` → segnala come cannibalizzazione
3. Restituisce array di `{ a, b, intent }` — coppie in conflitto

### Stopwords (STOP set)
Parole funzionali italiano + inglese essenziali: `di, il, la, per, con, the, for, best, come, cosa...`

### Threshold
`>= 2` parole in comune (escluse stopwords e token ≤ 2 caratteri).
Scelto empiricamente: 1 parola genera troppi falsi positivi, 3 troppi falsi negativi.

## Razionale

Il dataset è piccolo (le keyword di un singolo cliente, tipicamente < 200).
La complessità è O(n²) per intent group, accettabile a queste dimensioni.

Il calcolo è reattivo: si ricalcola ogni volta che la lista keyword cambia,
senza round-trip al server. L'esperienza utente è migliore.

## Conseguenze

- Se in futuro serve cannibalizzazione lato server (es. per report asincroni),
  la logica va estratta e duplicata o spostata in un modulo condiviso
- Il threshold di 2 parole è hard-coded — se produce troppi falsi positivi
  nella pratica, va esposto come parametro configurabile
- L'algoritmo non considera sinonimi o varianti morfologiche (es. "scarpa" ≠ "scarpe")
