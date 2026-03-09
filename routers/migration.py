import csv
import io
import json
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from openai import AsyncOpenAI

from auth import get_current_user

router = APIRouter()

# ══════════════════════════════════════════════
#  MODELLI
# ══════════════════════════════════════════════

class MigrationResult(BaseModel):
    old_url: str
    old_title: str
    old_h1: str
    old_inlinks: int
    new_url: Optional[str]
    new_title: Optional[str]
    confidence: int
    match_type: str  # "exact" | "slug" | "gpt" | "no_match"
    reason: Optional[str]


class ExportRequest(BaseModel):
    results: list[MigrationResult]
    old_domain: str
    new_domain: str


# ══════════════════════════════════════════════
#  UTILITY — parsing CSV Screaming Frog
# ══════════════════════════════════════════════

def _parse_screaming_frog_csv(content: bytes, domain: str) -> list[dict]:
    """
    Parsa un CSV Screaming Frog.
    Filtra righe: Content Type contiene 'text/html' AND Status Code == 200.
    Ritorna lista di dict con: address (slug), title, h1, inlinks.
    """
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))

    pages = []
    domain_stripped = domain.rstrip("/")

    for row in reader:
        content_type = row.get("Content Type", "")
        status_code = row.get("Status Code", "")

        if "text/html" not in content_type:
            continue
        try:
            if int(status_code) != 200:
                continue
        except (ValueError, TypeError):
            continue

        address = row.get("Address", "").strip()
        if not address:
            continue

        # Normalizza URL — rimuovi dominio, tieni solo slug
        if address.startswith(domain_stripped):
            slug = address[len(domain_stripped):]
        else:
            # Prova rimozione generica di schema+host
            from urllib.parse import urlparse
            parsed = urlparse(address)
            slug = parsed.path
            if parsed.query:
                slug += "?" + parsed.query

        if not slug:
            slug = "/"

        try:
            inlinks = int(row.get("Inlinks", 0) or 0)
        except (ValueError, TypeError):
            inlinks = 0

        pages.append({
            "address": slug,
            "title": row.get("Title 1", "").strip(),
            "h1": row.get("H1-1", "").strip(),
            "inlinks": inlinks,
        })

    return pages


# ══════════════════════════════════════════════
#  UTILITY — slug tokenizzazione e overlap
# ══════════════════════════════════════════════

def _tokenize_slug(slug: str) -> set[str]:
    """Split slug su '/' e '-', filtra token vuoti."""
    import re
    tokens = re.split(r"[/\-_]", slug.lower())
    return {t for t in tokens if len(t) > 1}


def _slug_overlap(slug_a: str, slug_b: str) -> float:
    """Calcola overlap token tra due slug. Ritorna valore 0.0–1.0."""
    tokens_a = _tokenize_slug(slug_a)
    tokens_b = _tokenize_slug(slug_b)
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union)


# ══════════════════════════════════════════════
#  UTILITY — GPT-4o matching semantico
# ══════════════════════════════════════════════

async def _gpt_match_batch(
    pages_to_analyze: list[dict],
    new_pages: list[dict],
    api_key: str,
) -> dict[str, dict]:
    """
    Chiama GPT-4o per fare matching semantico.
    Processa in batch da 20 pagine. Ritorna dict old_url -> {new_url, confidence, reason}.
    """
    client = AsyncOpenAI(api_key=api_key)
    results: dict[str, dict] = {}

    BATCH_SIZE = 20

    for i in range(0, len(pages_to_analyze), BATCH_SIZE):
        batch = pages_to_analyze[i : i + BATCH_SIZE]

        # Costruisce prompt per il batch
        pages_prompt_parts = []
        for idx, page in enumerate(batch):
            # Pre-filtro: top 10 candidate per similarità token
            candidates_sorted = sorted(
                new_pages,
                key=lambda p: _slug_overlap(page["address"], p["address"]),
                reverse=True,
            )[:10]

            candidates_text = "\n".join(
                f'  {j+1}. URL: {c["address"]} | Title: {c["title"]} | H1: {c["h1"]}'
                for j, c in enumerate(candidates_sorted)
            )

            pages_prompt_parts.append(
                f'PAGINA {idx+1}:\n'
                f'  URL vecchio: {page["address"]}\n'
                f'  Title: {page["title"]}\n'
                f'  H1: {page["h1"]}\n'
                f'CANDIDATE SITO NUOVO:\n{candidates_text}\n'
            )

            # Salva le candidate per recuperare i dettagli dopo
            page["_candidates"] = candidates_sorted

        user_prompt = (
            "Analizza ogni pagina del sito VECCHIO e trova la migliore destinazione redirect "
            "tra le candidate del sito NUOVO.\n\n"
            + "\n---\n".join(pages_prompt_parts)
            + "\n\nRispondi con un array JSON (un oggetto per ogni pagina, nello stesso ordine):\n"
            '[\n  {"match_url": "/slug" o null, "confidence": 0-100, "reason": "..."},\n  ...\n]'
        )

        try:
            response = await client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Sei un SEO specialist esperto in migrazioni di siti web.\n"
                            "Ti vengono fornite:\n"
                            "- Una pagina del sito VECCHIO con Title e H1\n"
                            "- Una lista di pagine candidate del sito NUOVO con Title e H1\n\n"
                            "Il tuo compito è identificare quale pagina del sito nuovo è la "
                            "migliore destinazione per il redirect 301 della pagina vecchia.\n"
                            "Rispondi SOLO in JSON, senza testo aggiuntivo:\n"
                            "Se nessuna pagina del nuovo sito è semanticamente adeguata come "
                            "destinazione redirect, restituisci match_url: null e confidence: 0."
                        ),
                    },
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
            )

            raw = response.choices[0].message.content or "[]"
            # GPT risponde con json_object — potrebbe essere {"results": [...]} o direttamente [...]
            parsed = json.loads(raw)

            # Normalizza risposta: potrebbe essere lista o dict con chiave lista
            if isinstance(parsed, list):
                gpt_list = parsed
            else:
                # Trova la prima chiave il cui valore è una lista
                gpt_list = next(
                    (v for v in parsed.values() if isinstance(v, list)), []
                )

            for idx, page in enumerate(batch):
                if idx >= len(gpt_list):
                    results[page["address"]] = {"new_url": None, "confidence": 0, "reason": None}
                    continue

                item = gpt_list[idx]
                if not isinstance(item, dict):
                    results[page["address"]] = {"new_url": None, "confidence": 0, "reason": None}
                    continue

                match_url = item.get("match_url")
                confidence = int(item.get("confidence", 0))
                reason = item.get("reason")

                # Verifica che match_url esista tra le candidate
                if match_url:
                    candidate_slugs = {c["address"] for c in page.get("_candidates", [])}
                    if match_url not in candidate_slugs:
                        match_url = None
                        confidence = 0

                results[page["address"]] = {
                    "new_url": match_url,
                    "confidence": confidence,
                    "reason": reason,
                }

        except Exception as e:
            # In caso di errore GPT, segna tutte le pagine del batch come no_match
            for page in batch:
                results[page["address"]] = {
                    "new_url": None,
                    "confidence": 0,
                    "reason": f"Errore GPT: {str(e)[:50]}",
                }

    return results


# ══════════════════════════════════════════════
#  ENDPOINT — analisi migrazione
# ══════════════════════════════════════════════

@router.post("/analyze")
async def analyze_migration(
    old_csv: UploadFile = File(...),
    new_csv: UploadFile = File(...),
    old_domain: str = Form(...),
    new_domain: str = Form(...),
    _user=Depends(get_current_user),
    x_openai_key: Optional[str] = Header(default=None),
):
    """
    Analizza due CSV Screaming Frog (sito vecchio e nuovo) e genera
    il mapping dei redirect 301 usando URL matching + GPT-4o semantico.
    """
    if not x_openai_key:
        raise HTTPException(status_code=400, detail="API key OpenAI mancante (header X-OpenAI-Key)")

    # ── Step 1: Parsing CSV ──────────────────────
    old_content = await old_csv.read()
    new_content = await new_csv.read()

    old_pages = _parse_screaming_frog_csv(old_content, old_domain)
    new_pages = _parse_screaming_frog_csv(new_content, new_domain)

    if not old_pages:
        raise HTTPException(status_code=400, detail="CSV sito vecchio vuoto o non valido (nessuna pagina HTML 200)")
    if not new_pages:
        raise HTTPException(status_code=400, detail="CSV sito nuovo vuoto o non valido (nessuna pagina HTML 200)")

    # Indicizza sito nuovo per lookup rapido
    new_by_slug: dict[str, dict] = {p["address"]: p for p in new_pages}

    # ── Step 2: URL Matching ─────────────────────
    results: list[dict] = []
    to_gpt: list[dict] = []

    stats = {"exact": 0, "slug": 0, "gpt": 0, "no_match": 0}

    for old_page in old_pages:
        old_slug = old_page["address"]

        # Livello 1 — Match esatto
        if old_slug in new_by_slug:
            new_page = new_by_slug[old_slug]
            results.append({
                "old_url": old_slug,
                "old_title": old_page["title"],
                "old_h1": old_page["h1"],
                "old_inlinks": old_page["inlinks"],
                "new_url": new_page["address"],
                "new_title": new_page["title"],
                "confidence": 100,
                "match_type": "exact",
                "reason": "Match esatto slug",
            })
            stats["exact"] += 1
            continue

        # Livello 2 — Match slug parziale
        best_overlap = 0.0
        best_new_page = None
        for new_page in new_pages:
            overlap = _slug_overlap(old_slug, new_page["address"])
            if overlap > best_overlap:
                best_overlap = overlap
                best_new_page = new_page

        if best_overlap >= 0.8 and best_new_page:
            results.append({
                "old_url": old_slug,
                "old_title": old_page["title"],
                "old_h1": old_page["h1"],
                "old_inlinks": old_page["inlinks"],
                "new_url": best_new_page["address"],
                "new_title": best_new_page["title"],
                "confidence": 85,
                "match_type": "slug",
                "reason": f"Overlap token slug {int(best_overlap*100)}%",
            })
            stats["slug"] += 1
        elif best_overlap >= 0.6 and best_new_page:
            results.append({
                "old_url": old_slug,
                "old_title": old_page["title"],
                "old_h1": old_page["h1"],
                "old_inlinks": old_page["inlinks"],
                "new_url": best_new_page["address"],
                "new_title": best_new_page["title"],
                "confidence": 65,
                "match_type": "slug",
                "reason": f"Overlap token slug {int(best_overlap*100)}%",
            })
            stats["slug"] += 1
        elif best_overlap >= 0.4 and best_new_page:
            results.append({
                "old_url": old_slug,
                "old_title": old_page["title"],
                "old_h1": old_page["h1"],
                "old_inlinks": old_page["inlinks"],
                "new_url": best_new_page["address"],
                "new_title": best_new_page["title"],
                "confidence": 40,
                "match_type": "slug",
                "reason": f"Overlap token slug {int(best_overlap*100)}%",
            })
            stats["slug"] += 1
        else:
            # Livello 3 — da analizzare con GPT
            to_gpt.append(old_page)

    # ── Step 3: GPT-4o matching semantico ───────
    if to_gpt and x_openai_key:
        gpt_results = await _gpt_match_batch(to_gpt, new_pages, x_openai_key)

        for old_page in to_gpt:
            old_slug = old_page["address"]
            gpt_match = gpt_results.get(old_slug, {})
            match_url = gpt_match.get("new_url")
            confidence = gpt_match.get("confidence", 0)
            reason = gpt_match.get("reason")

            if match_url and confidence > 0:
                new_page = new_by_slug.get(match_url, {})
                results.append({
                    "old_url": old_slug,
                    "old_title": old_page["title"],
                    "old_h1": old_page["h1"],
                    "old_inlinks": old_page["inlinks"],
                    "new_url": match_url,
                    "new_title": new_page.get("title"),
                    "confidence": confidence,
                    "match_type": "gpt",
                    "reason": reason,
                })
                stats["gpt"] += 1
            else:
                results.append({
                    "old_url": old_slug,
                    "old_title": old_page["title"],
                    "old_h1": old_page["h1"],
                    "old_inlinks": old_page["inlinks"],
                    "new_url": None,
                    "new_title": None,
                    "confidence": 0,
                    "match_type": "no_match",
                    "reason": reason,
                })
                stats["no_match"] += 1
    else:
        # Nessuna GPT key o nessuna pagina da analizzare
        for old_page in to_gpt:
            results.append({
                "old_url": old_page["address"],
                "old_title": old_page["title"],
                "old_h1": old_page["h1"],
                "old_inlinks": old_page["inlinks"],
                "new_url": None,
                "new_title": None,
                "confidence": 0,
                "match_type": "no_match",
                "reason": None,
            })
            stats["no_match"] += 1

    # ── Step 4: Risultato finale ─────────────────
    matched = stats["exact"] + stats["slug"] + stats["gpt"]
    no_match = stats["no_match"]

    return {
        "total": len(results),
        "matched": matched,
        "no_match": no_match,
        "results": results,
        "stats": stats,
    }


# ══════════════════════════════════════════════
#  ENDPOINT — export CSV
# ══════════════════════════════════════════════

@router.post("/export-csv")
def export_csv(
    data: ExportRequest,
    _user=Depends(get_current_user),
):
    """
    Genera e ritorna un CSV con il mapping completo dei redirect.
    Gli URL completi si ottengono riattaccando i domini agli slug.
    """
    old_domain = data.old_domain.rstrip("/")
    new_domain = data.new_domain.rstrip("/")

    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow([
        "URL vecchio (completo)",
        "URL nuovo (completo)",
        "Confidenza %",
        "Tipo match",
        "Motivo",
        "Title vecchio",
        "Title nuovo",
        "H1 vecchio",
        "H1 nuovo",
        "Inlinks",
    ])

    # Righe
    for r in data.results:
        old_full = old_domain + r.old_url
        new_full = (new_domain + r.new_url) if r.new_url else ""

        match_type_label = {
            "exact": "Esatto",
            "slug": "Slug",
            "gpt": "GPT",
            "no_match": "Nessuno",
        }.get(r.match_type, r.match_type)

        writer.writerow([
            old_full,
            new_full,
            r.confidence,
            match_type_label,
            r.reason or "",
            r.old_title,
            r.new_title or "",
            r.old_h1,
            "",  # new_h1 non nel modello, lascia vuoto
            r.old_inlinks,
        ])

    csv_bytes = output.getvalue().encode("utf-8-sig")  # BOM per Excel
    return StreamingResponse(
        io.BytesIO(csv_bytes),
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=migration_mapping.csv",
        },
    )
