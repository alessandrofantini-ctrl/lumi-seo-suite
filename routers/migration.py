import csv
import io
import json
import re
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
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
    new_url: Optional[str] = None
    new_title: Optional[str] = None
    new_domain: Optional[str] = None        # dominio di destinazione (multilingual)
    confidence: int
    match_type: str   # exact | slug | gpt | no_match | eliminated | consolidated
    reason: Optional[str] = None
    language_code: Optional[str] = None     # codice lingua (multilingual)


class ExportRequest(BaseModel):
    results: list[MigrationResult]
    old_domain: str
    new_domain: Optional[str] = None        # usato come fallback per migrazione semplice


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
        if domain_stripped and address.startswith(domain_stripped):
            slug = address[len(domain_stripped):]
        else:
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
    """Split slug su '/', '-', '_', filtra token vuoti."""
    tokens = re.split(r"[/\-_]", slug.lower())
    return {t for t in tokens if len(t) > 1}


def _slug_overlap(slug_a: str, slug_b: str) -> float:
    """Calcola overlap token (Jaccard) tra due slug. Ritorna 0.0–1.0."""
    tokens_a = _tokenize_slug(slug_a)
    tokens_b = _tokenize_slug(slug_b)
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union)


# ══════════════════════════════════════════════
#  UTILITY — strip prefisso lingua
# ══════════════════════════════════════════════

def _strip_lang_prefix(slug: str, prefix: str) -> str:
    """
    Rimuove il prefisso subdirectory lingua dallo slug.
    es. _strip_lang_prefix('/it/guida-seo', '/it/') → '/guida-seo'
    """
    p = "/" + prefix.strip("/")   # normalizza: '/it/' → '/it'
    if slug == p or slug == p + "/":
        return "/"
    if slug.startswith(p + "/"):
        return slug[len(p):]      # '/it/guida-seo' → '/guida-seo'
    return slug


# ══════════════════════════════════════════════
#  UTILITY — GPT-4o matching semantico
# ══════════════════════════════════════════════

async def _gpt_match_batch(
    pages_to_analyze: list[dict],   # "address", "match_slug", "title", "h1"
    new_pages: list[dict],           # "address", "match_slug", "title", "h1"
    api_key: str,
) -> dict[str, dict]:                # keyed by old page "address"
    """
    Chiama GPT-4o per matching semantico su match_slug.
    Batch da 20 pagine. Pre-filtro top 10 candidate per similarità token.
    Ritorna dict: old_address → {new_url, new_title, confidence, reason}.
    """
    client = AsyncOpenAI(api_key=api_key)
    results: dict[str, dict] = {}
    BATCH_SIZE = 20

    for i in range(0, len(pages_to_analyze), BATCH_SIZE):
        batch = pages_to_analyze[i : i + BATCH_SIZE]
        pages_prompt_parts = []

        for idx, page in enumerate(batch):
            candidates_sorted = sorted(
                new_pages,
                key=lambda p: _slug_overlap(page["match_slug"], p["match_slug"]),
                reverse=True,
            )[:10]

            candidates_text = "\n".join(
                f'  {j+1}. URL: {c["match_slug"]} | Title: {c["title"]} | H1: {c["h1"]}'
                for j, c in enumerate(candidates_sorted)
            )
            pages_prompt_parts.append(
                f'PAGINA {idx+1}:\n'
                f'  URL vecchio: {page["match_slug"]}\n'
                f'  Title: {page["title"]}\n'
                f'  H1: {page["h1"]}\n'
                f'CANDIDATE SITO NUOVO:\n{candidates_text}\n'
            )
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
                            "Rispondi SOLO in JSON, senza testo aggiuntivo.\n"
                            "Se nessuna pagina è semanticamente adeguata, "
                            "restituisci match_url: null e confidence: 0."
                        ),
                    },
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
            )

            raw = response.choices[0].message.content or "{}"
            parsed = json.loads(raw)

            if isinstance(parsed, list):
                gpt_list = parsed
            else:
                gpt_list = next((v for v in parsed.values() if isinstance(v, list)), [])

            for idx, page in enumerate(batch):
                empty = {"new_url": None, "new_title": None, "confidence": 0, "reason": None}
                if idx >= len(gpt_list):
                    results[page["address"]] = empty
                    continue

                item = gpt_list[idx]
                if not isinstance(item, dict):
                    results[page["address"]] = empty
                    continue

                match_slug = item.get("match_url")
                confidence = int(item.get("confidence", 0))
                reason = item.get("reason")

                if match_slug:
                    candidate_by_slug = {c["match_slug"]: c for c in page.get("_candidates", [])}
                    if match_slug in candidate_by_slug:
                        matched_new = candidate_by_slug[match_slug]
                        results[page["address"]] = {
                            "new_url": matched_new["address"],
                            "new_title": matched_new["title"],
                            "confidence": confidence,
                            "reason": reason,
                        }
                    else:
                        results[page["address"]] = empty
                else:
                    results[page["address"]] = {"new_url": None, "new_title": None, "confidence": 0, "reason": reason}

        except Exception as e:
            for page in batch:
                results[page["address"]] = {
                    "new_url": None, "new_title": None,
                    "confidence": 0, "reason": f"Errore GPT: {str(e)[:50]}",
                }

    return results


# ══════════════════════════════════════════════
#  UTILITY — core matching (riusabile)
# ══════════════════════════════════════════════

async def _match_pages(
    old_pages: list[dict],   # "address", "match_slug", "title", "h1", "inlinks"
    new_pages: list[dict],   # "address", "match_slug", "title", "h1"
    api_key: str,
) -> tuple[list[dict], dict]:
    """
    Matching a 3 livelli su match_slug.
    Ritorna (result_dicts, stats). I result non includono language_code/new_domain.
    """
    new_by_match_slug: dict[str, dict] = {p["match_slug"]: p for p in new_pages}
    results: list[dict] = []
    to_gpt: list[dict] = []
    stats = {"exact": 0, "slug": 0, "gpt": 0, "no_match": 0}

    for old_page in old_pages:
        ms = old_page["match_slug"]

        # Livello 1 — Match esatto
        if ms in new_by_match_slug:
            new_page = new_by_match_slug[ms]
            results.append({
                "old_url": old_page["address"],
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
            overlap = _slug_overlap(ms, new_page["match_slug"])
            if overlap > best_overlap:
                best_overlap = overlap
                best_new_page = new_page

        if best_overlap >= 0.8 and best_new_page:
            conf, match_type = 85, "slug"
        elif best_overlap >= 0.6 and best_new_page:
            conf, match_type = 65, "slug"
        elif best_overlap >= 0.4 and best_new_page:
            conf, match_type = 40, "slug"
        else:
            best_new_page = None
            conf, match_type = 0, ""

        if best_new_page:
            results.append({
                "old_url": old_page["address"],
                "old_title": old_page["title"],
                "old_h1": old_page["h1"],
                "old_inlinks": old_page["inlinks"],
                "new_url": best_new_page["address"],
                "new_title": best_new_page["title"],
                "confidence": conf,
                "match_type": match_type,
                "reason": f"Overlap token slug {int(best_overlap*100)}%",
            })
            stats["slug"] += 1
        else:
            to_gpt.append(old_page)

    # Livello 3 — GPT-4o
    if to_gpt and api_key:
        gpt_results = await _gpt_match_batch(to_gpt, new_pages, api_key)

        for old_page in to_gpt:
            gpt_match = gpt_results.get(old_page["address"], {})
            new_url = gpt_match.get("new_url")
            confidence = gpt_match.get("confidence", 0)
            reason = gpt_match.get("reason")

            if new_url and confidence > 0:
                results.append({
                    "old_url": old_page["address"],
                    "old_title": old_page["title"],
                    "old_h1": old_page["h1"],
                    "old_inlinks": old_page["inlinks"],
                    "new_url": new_url,
                    "new_title": gpt_match.get("new_title"),
                    "confidence": confidence,
                    "match_type": "gpt",
                    "reason": reason,
                })
                stats["gpt"] += 1
            else:
                results.append({
                    "old_url": old_page["address"],
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

    return results, stats


# ══════════════════════════════════════════════
#  ENDPOINT — analisi migrazione
# ══════════════════════════════════════════════

@router.post("/analyze")
async def analyze_migration(
    request: Request,
    _user=Depends(get_current_user),
    x_openai_key: Optional[str] = Header(default=None),
):
    """
    Analizza CSV Screaming Frog e genera mapping redirect 301.
    Supporta migrazione semplice (1 dominio) e multilingua (N lingue/domini).

    Multipart form-data:
      config          — JSON string con migration_type, old_domain, new_domain, language_mappings
      old_csv         — CSV sito vecchio (sempre presente)
      new_csv_default — CSV sito nuovo (solo migrazione semplice)
      new_csv_{lang}  — CSV per ogni lingua (migrazione multilingua)
    """
    if not x_openai_key:
        raise HTTPException(status_code=400, detail="API key OpenAI mancante (header X-OpenAI-Key)")

    form = await request.form()

    config_str = form.get("config")
    if not config_str:
        raise HTTPException(status_code=400, detail="Campo 'config' mancante")
    try:
        config = json.loads(str(config_str))
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Campo 'config' non è JSON valido")

    migration_type = config.get("migration_type", "simple")
    old_domain = config.get("old_domain", "").rstrip("/")

    old_csv_file = form.get("old_csv")
    if not old_csv_file or not hasattr(old_csv_file, "read"):
        raise HTTPException(status_code=400, detail="Campo 'old_csv' mancante")

    old_content = await old_csv_file.read()
    old_pages_raw = _parse_screaming_frog_csv(old_content, old_domain)

    if not old_pages_raw:
        raise HTTPException(
            status_code=400,
            detail="CSV sito vecchio vuoto o non valido (nessuna pagina HTML 200)",
        )

    # ── Migrazione semplice ───────────────────────────────────────────────────

    if migration_type == "simple":
        new_domain = config.get("new_domain", "").rstrip("/")
        new_csv_file = form.get("new_csv_default") or form.get("new_csv")
        if not new_csv_file or not hasattr(new_csv_file, "read"):
            raise HTTPException(status_code=400, detail="CSV sito nuovo mancante (new_csv_default)")

        new_content = await new_csv_file.read()
        new_pages_raw = _parse_screaming_frog_csv(new_content, new_domain)
        if not new_pages_raw:
            raise HTTPException(
                status_code=400,
                detail="CSV sito nuovo vuoto o non valido (nessuna pagina HTML 200)",
            )

        old_pages = [{**p, "match_slug": p["address"]} for p in old_pages_raw]
        new_pages = [{**p, "match_slug": p["address"]} for p in new_pages_raw]

        match_results, stats = await _match_pages(old_pages, new_pages, x_openai_key)

        for r in match_results:
            r["language_code"] = None
            r["new_domain"] = new_domain if r.get("new_url") else None

        matched = stats["exact"] + stats["slug"] + stats["gpt"]
        return {
            "total": len(match_results),
            "matched": matched,
            "no_match": stats["no_match"],
            "eliminated": 0,
            "results": match_results,
            "stats": {**stats, "eliminated": 0, "consolidated": 0},
            "by_language": {},
        }

    # ── Migrazione multilingua ────────────────────────────────────────────────

    language_mappings = config.get("language_mappings", [])
    main_new_domain = config.get("new_domain", "").rstrip("/")

    # Step 1: Parsa tutti i CSV del sito nuovo (uno per lingua non eliminata)
    new_pages_by_lang: dict[str, list[dict]] = {}
    new_domain_by_lang: dict[str, str] = {}

    for lm in language_mappings:
        lang_code = lm.get("language_code", "")
        dest_type = lm.get("destination_type", "")
        dest_value = lm.get("destination_value", "").rstrip("/")

        if dest_type in ("eliminated", "consolidated"):
            continue

        csv_key = f"new_csv_{lang_code}"
        new_csv_file = form.get(csv_key)
        if not new_csv_file or not hasattr(new_csv_file, "read"):
            continue

        domain_for_parsing = dest_value if dest_type == "domain" else main_new_domain
        content = await new_csv_file.read()
        raw_pages = _parse_screaming_frog_csv(content, domain_for_parsing)

        # Calcola match_slug: strip prefisso per subdirectory
        pages = []
        for p in raw_pages:
            ms = (
                _strip_lang_prefix(p["address"], dest_value)
                if dest_type == "subdirectory" and dest_value
                else p["address"]
            )
            pages.append({**p, "match_slug": ms})

        new_pages_by_lang[lang_code] = pages
        new_domain_by_lang[lang_code] = dest_value if dest_type == "domain" else main_new_domain

    # Risolvi dominio per lingue consolidate (usa il dominio della lingua target)
    for lm in language_mappings:
        if lm.get("destination_type") == "consolidated":
            lang_code = lm.get("language_code", "")
            target = lm.get("target_language_code", "")
            new_domain_by_lang[lang_code] = new_domain_by_lang.get(target, main_new_domain)

    # Step 2: Associa ogni pagina vecchia alla sua lingua
    # Prima pass: lingue subdirectory (prefix-based), poi domain (catch-all)
    claimed_addresses: set[str] = set()
    lang_old_pages_map: dict[str, list[dict]] = {}

    for lm in language_mappings:
        if lm.get("source_type") != "subdirectory":
            continue
        lang_code = lm.get("language_code", "")
        src_value = lm.get("source_value", "")
        prefix_clean = "/" + src_value.strip("/")

        pages = []
        for p in old_pages_raw:
            addr = p["address"]
            if addr == prefix_clean or addr.startswith(prefix_clean + "/"):
                ms = _strip_lang_prefix(addr, src_value)
                pages.append({**p, "match_slug": ms})
                claimed_addresses.add(addr)
        lang_old_pages_map[lang_code] = pages

    for lm in language_mappings:
        if lm.get("source_type") != "domain":
            continue
        lang_code = lm.get("language_code", "")
        pages = [
            {**p, "match_slug": p["address"]}
            for p in old_pages_raw
            if p["address"] not in claimed_addresses
        ]
        lang_old_pages_map[lang_code] = pages

    # Step 3: Matching per ogni lingua
    all_results: list[dict] = []
    all_stats: dict[str, int] = {
        "exact": 0, "slug": 0, "gpt": 0,
        "no_match": 0, "eliminated": 0, "consolidated": 0,
    }
    by_language: dict[str, dict] = {}

    for lm in language_mappings:
        lang_code = lm.get("language_code", "")
        dest_type = lm.get("destination_type", "")
        target_lang = lm.get("target_language_code", "")

        old_lang_pages = lang_old_pages_map.get(lang_code, [])
        lang_stat: dict[str, int] = {"total": len(old_lang_pages), "matched": 0, "no_match": 0}

        # Lingua eliminata — nessun redirect
        if dest_type == "eliminated":
            for old_page in old_lang_pages:
                all_results.append({
                    "old_url": old_page["address"],
                    "old_title": old_page["title"],
                    "old_h1": old_page["h1"],
                    "old_inlinks": old_page["inlinks"],
                    "new_url": None,
                    "new_title": None,
                    "new_domain": None,
                    "confidence": 0,
                    "match_type": "eliminated",
                    "reason": "Lingua eliminata — nessun redirect",
                    "language_code": lang_code,
                })
            all_stats["eliminated"] += len(old_lang_pages)
            lang_stat["eliminated"] = len(old_lang_pages)
            by_language[lang_code] = lang_stat
            continue

        # Lingue con match
        if dest_type == "consolidated":
            new_pages = new_pages_by_lang.get(target_lang, [])
        else:
            new_pages = new_pages_by_lang.get(lang_code, [])

        dest_new_domain = new_domain_by_lang.get(lang_code, main_new_domain)

        if not new_pages:
            for old_page in old_lang_pages:
                all_results.append({
                    "old_url": old_page["address"],
                    "old_title": old_page["title"],
                    "old_h1": old_page["h1"],
                    "old_inlinks": old_page["inlinks"],
                    "new_url": None,
                    "new_title": None,
                    "new_domain": None,
                    "confidence": 0,
                    "match_type": "no_match",
                    "reason": "Nessun CSV per la lingua di destinazione",
                    "language_code": lang_code,
                })
            all_stats["no_match"] += len(old_lang_pages)
            lang_stat["no_match"] = len(old_lang_pages)
            by_language[lang_code] = lang_stat
            continue

        match_results, lang_stats = await _match_pages(old_lang_pages, new_pages, x_openai_key)

        lang_matched = lang_stats["exact"] + lang_stats["slug"] + lang_stats["gpt"]

        for r in match_results:
            r["language_code"] = lang_code
            r["new_domain"] = dest_new_domain if r.get("new_url") else None

            # Lingue consolidate: rinomina match_type nei risultati con match
            if dest_type == "consolidated" and r["match_type"] not in ("no_match",):
                r["match_type"] = "consolidated"

        all_results.extend(match_results)

        # Aggiorna statistiche globali
        if dest_type == "consolidated":
            all_stats["consolidated"] += lang_matched
        else:
            all_stats["exact"] += lang_stats["exact"]
            all_stats["slug"] += lang_stats["slug"]
            all_stats["gpt"] += lang_stats["gpt"]
        all_stats["no_match"] += lang_stats["no_match"]

        lang_stat["matched"] = lang_matched
        lang_stat["no_match"] = lang_stats["no_match"]
        by_language[lang_code] = lang_stat

    matched = (
        all_stats["exact"]
        + all_stats["slug"]
        + all_stats["gpt"]
        + all_stats["consolidated"]
    )

    return {
        "total": len(all_results),
        "matched": matched,
        "no_match": all_stats["no_match"],
        "eliminated": all_stats["eliminated"],
        "results": all_results,
        "stats": all_stats,
        "by_language": by_language,
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
    Genera CSV con il mapping completo dei redirect.
    Per migrazione multilingua usa r.new_domain per risultato.
    Per migrazione semplice usa data.new_domain come fallback.
    """
    old_domain = data.old_domain.rstrip("/")
    fallback_new_domain = (data.new_domain or "").rstrip("/")

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "URL vecchio (completo)",
        "URL nuovo (completo)",
        "Dominio nuovo",
        "Lingua",
        "Confidenza %",
        "Tipo match",
        "Motivo",
        "Title vecchio",
        "Title nuovo",
        "H1 vecchio",
        "H1 nuovo",
        "Inlinks",
    ])

    for r in data.results:
        old_full = old_domain + r.old_url
        effective_new_domain = (r.new_domain or fallback_new_domain).rstrip("/")
        new_full = (effective_new_domain + r.new_url) if r.new_url else ""

        match_type_label = {
            "exact":       "Esatto",
            "slug":        "Slug",
            "gpt":         "GPT",
            "no_match":    "Nessuno",
            "eliminated":  "Eliminata",
            "consolidated": "Consolidata",
        }.get(r.match_type, r.match_type)

        lang_label = r.language_code.upper() if r.language_code else ""

        writer.writerow([
            old_full,
            new_full,
            effective_new_domain if r.new_url else "",
            lang_label,
            r.confidence,
            match_type_label,
            r.reason or ("Lingua eliminata — pagina rimossa" if r.match_type == "eliminated" else ""),
            r.old_title,
            r.new_title or "",
            r.old_h1,
            "",  # H1 nuovo non nel modello
            r.old_inlinks,
        ])

    csv_bytes = output.getvalue().encode("utf-8-sig")  # BOM per Excel
    return StreamingResponse(
        io.BytesIO(csv_bytes),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=migration_mapping.csv"},
    )
