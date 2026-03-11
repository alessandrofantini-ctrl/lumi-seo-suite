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
        "Standard":             "1200–1600",
        "Long form":            "1800–2500",
        "Authority guide":      "2500–3500",
    }.get(label, "1800–2500")

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

    competitor_compact = []
    for c in competitor_results[:8]:
        competitor_compact.append({
            "url":        c.get("url"),
            "title":      truncate(c.get("title", ""), 120),
            "h1":         truncate(c.get("h1", ""), 120),
            "h2":         [truncate(x, 90) for x in (c.get("h2") or [])[:12]],
            "h3":         [truncate(x, 90) for x in (c.get("h3") or [])[:12]],
            "word_count": c.get("word_count", 0),
        })

    system_prompt = (
        "Sei un Senior SEO strategist. Produci brief pratici e brevi, orientati all'esecuzione. "
        "Non inserire teoria: solo ciò che serve per scrivere una pagina migliore dei competitor. "
        "Il brief deve essere costruito sui prodotti e servizi REALI del cliente, non su contenuti generici."
    )

    user_prompt = f"""
Keyword principale: "{keyword}"
Mercato: {market}
Lingua output SEO (meta/h1/h2/h3): {target_lang}
Intento: {intent}

SERP:
- Features: {serp_snapshot.get("features", [])}
- PAA: {serp_snapshot.get("paa", [])[:10]}
- Related searches: {serp_snapshot.get("related_searches", [])[:12]}

Competitor (sintesi):
{json.dumps(competitor_compact, ensure_ascii=False)}

Pattern competitor:
- H2 ricorrenti: {aggregated.get("top_h2", [])}
- Termini ricorrenti: {aggregated.get("top_terms", [])[:18]}
- Domande ricorrenti: {aggregated.get("top_questions", [])}

=== CONTESTO CLIENTE (PRIORITÀ MASSIMA) ===
{client_context if client_context else "Nessun profilo cliente selezionato."}

=== REGOLE ===
1) Output in ITALIANO per le istruzioni, meta/title/H1/H2/H3 in lingua {target_lang}.
2) Sentence case per titoli (solo prima lettera maiuscola).
3) Meta title: preferisci "keyword | Brand", max 60 caratteri, 3 varianti.
4) Meta description: max 155 caratteri, 3 varianti.
5) H2/H3 devono essere rilevanti per ciò che VENDE il cliente.
6) Output compatto.

FORMATO RISPOSTA:
## meta
- title (v1): ...
- title (v2): ...
- title (v3): ...
- description (v1): ...
- description (v2): ...
- description (v3): ...

## h1
- ...

## outline (H2/H3)
Massimo 10 H2. Per ogni H2: 2-4 H3 + Nota (IT) su cosa scrivere.

## keyword set
- primary: ...
- secondary (max 12): ...

## faq
5 domande (in {target_lang}) + risposta 1 frase

## cta
3 CTA brevi coerenti con intento "{intent}"
"""

    resp = await client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        temperature=0.5,
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

Dati fissi:
- Brand: "{brand_name}"
- Lunghezza minima: {word_target} parole
- URL CTA (se presente): "{target_page_url}"

Scrivi l'articolo completo seguendo esattamente l'outline del brief.

Requisiti:
1) Introduzione 150-220 parole con keyword naturale e promessa al lettore.
2) Per ogni H2: esempio concreto, checklist o mini-framework, errore comune da evitare.
3) Massimo 1-2 tabelle solo se aiutano una decisione.
4) FAQ: 5 domande orientate a dubbi reali (costi, tempi, rischi).
5) CTA finale: 2-3 CTA brevi e concrete.

Output: solo Markdown dell'articolo, senza commenti extra.
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
