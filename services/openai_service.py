import os
import re
import json
from openai import AsyncOpenAI

def _openai_client(api_key: str | None = None) -> AsyncOpenAI:
    key = api_key or os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OpenAI API key non configurata. Inseriscila nelle Impostazioni.")
    return AsyncOpenAI(api_key=key)

# ══════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════

def truncate(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s[:n]

def get_word_target(label: str) -> str:
    return {
        "Standard":        "1200–1600",
        "Long form":       "1800–2500",
        "Authority guide": "2500–3500",
    }.get(label, "1800–2500")

def build_client_context(client_data: dict, kw_list: list) -> str:
    """Costruisce il contesto cliente filtrando i campi vuoti."""
    if not client_data:
        return "Nessun profilo cliente selezionato."

    fields = {
        "Cliente":           client_data.get("name"),
        "Settore":           client_data.get("sector"),
        "Zona geografica":   client_data.get("geo"),
        "Target audience":   client_data.get("target_audience"),
        "USP":               client_data.get("usp"),
        "Tone of voice":     client_data.get("tone_of_voice"),
        "Prodotti/servizi":  client_data.get("products_services"),
        "Note strategiche":  client_data.get("notes"),
        "Keyword già usate": ", ".join(kw_list) if kw_list else None,
    }

    lines = [
        f"- {k}: {v}"
        for k, v in fields.items()
        if v and str(v).strip()
    ]

    return "\n".join(lines) if lines else "Nessun profilo cliente selezionato."


# ══════════════════════════════════════════════
#  AUTO-GENERAZIONE PROFILO CLIENTE
# ══════════════════════════════════════════════

async def generate_profile_from_url(base_url: str, pages_data: list, api_key: str | None = None) -> dict:
    client = _openai_client(api_key)

    all_text_parts = []
    for label, page in pages_data:
        snippet = (
            f"[{label}]\n"
            f"Title: {page.get('title','')}\n"
            f"H1: {page.get('h1','')}\n"
            f"H2: {str(page.get('h2s', []))}\n"
            f"{page.get('text','')[:1200]}"
        )
        all_text_parts.append(snippet)

    combined_text = "\n\n---\n\n".join(all_text_parts)

    prompt = f"""Analizza il contenuto di questo sito web e restituisci SOLO un oggetto JSON valido:

{{
  "name": "nome azienda/brand",
  "sector": "settore di attività",
  "brand_name": "nome brand commerciale",
  "products_services": "prodotti/servizi uno per riga",
  "usp": "punti di forza in 2-3 frasi",
  "target_audience": "cliente tipo",
  "geo": "zona geografica",
  "tone_of_voice": "uno tra: Autorevole & tecnico | Empatico & problem solving | Diretto & commerciale",
  "notes": "note strategiche SEO (max 2 frasi)"
}}

Contenuto:
{combined_text[:6000]}

Rispondi SOLO con il JSON, senza backtick o altro testo."""

    resp = await client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "Sei un esperto SEO. Estrai informazioni strutturate da siti web."},
            {"role": "user",   "content": prompt},
        ],
        temperature=0.2,
    )

    raw = resp.choices[0].message.content.strip()
    raw = re.sub(r"^```json\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        return json.loads(raw)
    except Exception:
        return {}


# ══════════════════════════════════════════════
#  GENERAZIONE BRIEF SEO
# ══════════════════════════════════════════════

async def generate_seo_brief(
    keyword: str,
    market: str,
    market_params: dict,
    intent: str,
    client_context: str,
    serp_snapshot: dict,
    competitor_results: list,
    aggregated: dict,
    api_key: str | None = None,
) -> str:
    client = _openai_client(api_key)
    target_lang = market_params["hl"]

    # Deduplica H2 tra tutti i competitor
    all_h2: list[str] = []
    seen_h2: set[str] = set()
    for c in competitor_results[:6]:
        for h in (c.get("h2") or [])[:8]:
            clean = truncate(h, 80).lower().strip()
            if clean and clean not in seen_h2:
                seen_h2.add(clean)
                all_h2.append(truncate(h, 80))

    # Competitor compact — solo dati utili, senza H3
    competitor_compact = []
    for c in competitor_results[:6]:
        competitor_compact.append({
            "url":        c.get("url"),
            "title":      truncate(c.get("title", ""), 100),
            "h1":         truncate(c.get("h1", ""), 100),
            "word_count": c.get("word_count", 0),
            "h2_sample":  [truncate(x, 80) for x in (c.get("h2") or [])[:5]],
        })

    avg_word_count = round(
        sum(c.get("word_count", 0) for c in competitor_results[:6])
        / max(len(competitor_results[:6]), 1)
    )

    system_prompt = (
        "Sei un Senior SEO strategist che lavora per un'agenzia italiana. "
        "Il tuo compito è produrre brief operativi pronti per un copywriter: "
        "niente teoria, niente frasi generiche, niente claim senza fonte.\n\n"
        "Cosa NON fare mai:\n"
        "- Non copiare titoli dai competitor: riformula con l'angolo unico del cliente\n"
        "- Non usare: 'nel mondo di oggi', 'è fondamentale', 'come vedremo', 'guida completa'\n"
        "- Non lasciare H2/H3 vaghi: ogni sezione deve dire COSA scrivere, non solo l'argomento\n"
        "- Non ignorare i prodotti/servizi del cliente: ogni sezione deve agganciare l'offerta reale\n"
        "- Non inserire campi vuoti o placeholder nel brief\n\n"
        "Regola d'oro: se un copywriter legge il brief e non sa cosa scrivere in una sezione, il brief è sbagliato."
    )

    user_prompt = f"""
## INPUT

Keyword: "{keyword}"
Mercato: {market} | Lingua output (meta/titoli): {target_lang}
Intento: {intent}

---

## SERP FEATURES
- Elementi speciali: {serp_snapshot.get("features", [])}
- People Also Ask: {serp_snapshot.get("paa", [])[:8]}
- Ricerche correlate: {serp_snapshot.get("related_searches", [])[:10]}

---

## COMPETITOR (usa solo per capire i gap, non per copiare)
Word count medio: {avg_word_count} parole

{json.dumps(competitor_compact, ensure_ascii=False, indent=2)}

H2 ricorrenti nei competitor (da differenziare o superare):
{json.dumps(all_h2[:15], ensure_ascii=False)}

Termini semanticamente ricorrenti (includi naturalmente nel testo):
{json.dumps(aggregated.get("top_terms", [])[:15], ensure_ascii=False)}

---

## PROFILO CLIENTE (massima priorità — ogni sezione del brief deve riflettere questo)
{client_context}

---

## OUTPUT ATTESO

Produci il brief in questo formato esatto. Non aggiungere sezioni extra.

### META
Scrivi in {target_lang}. Sentence case. Keyword vicino all'inizio del title.
- title_v1: [max 60 car — formula: keyword | differenziatore brand]
- title_v2: [variante con beneficio esplicito]
- title_v3: [variante domanda o long tail]
- desc_v1: [max 155 car — benefit principale + CTA implicita]
- desc_v2: [max 155 car — angolo problema/soluzione]
- desc_v3: [max 155 car — angolo social proof o urgenza]

### H1
Un'unica proposta. Sentence case. In {target_lang}.
Deve differenziarsi dai competitor e riflettere l'angolo unico del cliente.

### OUTLINE
Max 8 H2. Per ogni H2:
- Titolo H2 (in {target_lang}, sentence case)
- 2–3 H3 (in {target_lang}, sentence case)
- Nota redazionale (in italiano): cosa scrivere in questa sezione, come agganciarla
  ai prodotti/servizi del cliente, quale angolo usare per differenziarsi dai competitor,
  tone of voice da applicare

### KEYWORD SET
- primary: {keyword}
- secondary: [max 10 keyword semanticamente correlate, in {target_lang}]
- LSI: [max 8 termini latenti ricavati dai competitor e dalle ricerche correlate]

### FAQ
5 domande reali (priorità alle PAA, poi dubbi tipici del target).
In {target_lang}. Risposta max 2 frasi per voce.
Ogni risposta deve menzionare almeno un prodotto/servizio del cliente se pertinente.

### CTA
3 CTA brevi (max 8 parole ciascuna), coerenti con intento "{intent}" e tone of voice del cliente.
"""

    resp = await client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        temperature=0.3,
        max_tokens=4096,
    )

    return resp.choices[0].message.content


# ══════════════════════════════════════════════
#  GENERAZIONE ARTICOLO
# ══════════════════════════════════════════════

async def generate_article(
    brief_text: str,
    brand_name: str,
    target_page_url: str,
    length: str,
    creativity: float,
    tone_of_voice: str = "",
    products_services: str = "",
    api_key: str | None = None,
) -> str:
    client = _openai_client(api_key)
    word_target = get_word_target(length)

    tone_instruction = f"\n- Tono di voce: {tone_of_voice}." if tone_of_voice else ""
    products_block = f"\n\nProdotti/servizi del cliente (priorità massima — menziona solo ciò che il cliente offre realmente):\n{products_services}" if products_services else ""

    system_prompt = f"""Sei un senior SEO copywriter.
Scrivi contenuti autorevoli ma concreti, senza frasi generiche.
Stile: chiaro, operativo, orientato a decisioni e casi reali.

Regole:
- Scrivi in italiano.
- Maiuscole: sentence case per H1/H2/H3.
- Evita claim numerici non supportati.
- Niente "come vedremo", "nel mondo di oggi", "rivoluzionare".{tone_instruction}"""

    user_prompt = f"""
Brief SEO:
{brief_text}{products_block}
    # Estrai tone of voice e prodotti dal brief se presenti
    tone_hint = ""
    products_hint = ""
    for line in brief_text.splitlines():
        if "tone of voice" in line.lower() and not tone_hint:
            tone_hint = line.strip()
        if ("prodotti" in line.lower() or "servizi" in line.lower()) and not products_hint:
            products_hint = line.strip()

    brand = brand_name if brand_name else "il cliente"

    system_prompt = f"""Sei un senior SEO copywriter italiano.
Scrivi contenuti concreti, autorevoli e orientati a chi deve prendere una decisione.

REGOLE ASSOLUTE:
- Scrivi SEMPRE in italiano
- Sentence case per tutti i titoli (H1, H2, H3)
- Segui esattamente l'outline del brief — non inventare sezioni, non saltarne
- Ogni H2 deve avere una struttura diversa dalle altre (non tutti paragrafi narrativi)
  Alterna: lista pratica / confronto / scenario reale / errore comune / mini-checklist
- Zero frasi di apertura generiche: niente "Nel mondo di oggi", "È fondamentale",
  "In questa guida", "Come vedremo"
- Zero claim numerici senza fonte (no "il 90% delle aziende...")
- Non menzionare mai competitor per nome

TONO:
{tone_hint if tone_hint else "Professionale, diretto, concreto. Scrivi come un esperto che parla a un cliente informato."}

BRAND: {brand}
{f"PRODOTTI/SERVIZI DA CITARE: {products_hint}" if products_hint else ""}
"""

    user_prompt = f"""
Brief SEO completo:
{brief_text}

---

ISTRUZIONI DI SCRITTURA:

Lunghezza minima: {word_target} parole
{f"URL per CTA: {target_page_url}" if target_page_url else ""}

**INTRODUZIONE (150–220 parole)**
Inizia con un hook diretto: una domanda provocatoria, un dato concreto,
o uno scenario riconoscibile dal target. NON iniziare con la keyword.
Seconda frase: aggancia il problema reale del lettore.
Chiudi l'intro con una promessa specifica su cosa troverà nell'articolo.

**CORPO DELL'ARTICOLO**
Segui esattamente l'outline H2/H3 del brief.
Per ogni H2:
- Applica il tone of voice indicato nel brief
- Includi almeno un riferimento concreto ai prodotti/servizi di {brand}
- Usa una struttura diversa dalle altre sezioni (lista / confronto / scenario / checklist)
- Se nel brief c'è una "Nota redazionale" per quella sezione, seguila alla lettera

**FAQ**
Usa le domande indicate nel brief.
Risposte dirette, max 3 frasi. Almeno 2 risposte devono citare
un prodotto/servizio specifico di {brand}.

**CONCLUSIONE (80–120 parole)**
Non riassumere l'articolo — è la parte che il lettore ricorda.
Struttura: 1) insight finale che cambia prospettiva, 2) CTA concreta e specifica.
{f"CTA deve linkare a: {target_page_url}" if target_page_url else ""}

Output: solo Markdown dell'articolo. Nessun commento, nessuna premessa.
"""

    resp = await client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        temperature=creativity,
        max_tokens=7500,
    )

    return resp.choices[0].message.content
