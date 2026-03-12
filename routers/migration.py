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
    old_title: str = ""
    old_h1: str = ""
    old_inlinks: int = 0
    new_url: Optional[str] = None
    new_title: Optional[str] = None
    target_domain: Optional[str] = None   # dominio nuovo di destinazione
    target_label: Optional[str] = None    # label opzionale del dominio nuovo
    confidence: int = 0
    match_type: str   # exact | slug | gpt | no_match | eliminated | consolidated | homepage
    reason: Optional[str] = None


class ExportRequest(BaseModel):
    results: list[MigrationResult]
    old_domain: str


# ══════════════════════════════════════════════
#  UTILITY — parsing CSV Screaming Frog
# ══════════════════════════════════════════════

def _parse_screaming_frog_csv(content: bytes, domain: str) -> list[dict]:
    """
    Parsa un CSV Screaming Frog.
    Filtra: Content Type contiene 'text/html' AND Status Code == 200.
    Ritorna lista di dict con: address (slug), title, h1, inlinks.
    """
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    pages = []
    domain_stripped = domain.rstrip("/")

    for row in reader:
        if "text/html" not in row.get("Content Type", ""):
            continue
        try:
            if int(row.get("Status Code", 0)) != 200:
                continue
        except (ValueError, TypeError):
            continue

        address = row.get("Address", "").strip()
        if not address:
            continue

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
    tokens = re.split(r"[/\-_]", slug.lower())
    return {t for t in tokens if len(t) > 1}


def _slug_overlap(slug_a: str, slug_b: str) -> float:
    """Jaccard overlap tra token di due slug. Ritorna 0.0–1.0."""
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
    _strip_lang_prefix('/it/guida-seo', '/it/') → '/guida-seo'
    """
    p = "/" + prefix.strip("/")
    if slug == p or slug == p + "/":
        return "/"
    if slug.startswith(p + "/"):
        return slug[len(p):]
    return slug


# ══════════════════════════════════════════════
#  UTILITY — language rule matching
# ══════════════════════════════════════════════

def _url_matches_rule(url: str, rule: dict) -> bool:
    """Controlla se un URL corrisponde a una language rule."""
    pattern = rule.get("pattern", "")
    if not pattern:
        return False
    if rule.get("pattern_type") == "subdirectory":
        prefix = "/" + pattern.strip("/")
        return url == prefix or url.startswith(prefix + "/")
    else:  # domain
        return pattern in url


# ══════════════════════════════════════════════
#  UTILITY — GPT-4o matching semantico
# ══════════════════════════════════════════════

async def _gpt_match_batch(
    pages_to_analyze: list[dict],   # "address", "match_slug", "title", "h1"
    new_pages: list[dict],           # "address", "match_slug", "title", "h1"
    api_key: str,
) -> dict[str, dict]:                # keyed by old page "address"
    """
    GPT-4o matching semantico in batch da 20 pagine.
    Usa match_slug per confronto, restituisce address del sito nuovo.
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
                            "Identifica la migliore pagina del sito NUOVO come destinazione "
                            "del redirect 301 per ogni pagina del sito VECCHIO.\n"
                            "Rispondi SOLO in JSON. Se nessuna pagina è adeguata: "
                            "match_url: null, confidence: 0."
                        ),
                    },
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
            )

            raw = response.choices[0].message.content or "{}"
            parsed = json.loads(raw)
            gpt_list = parsed if isinstance(parsed, list) else next(
                (v for v in parsed.values() if isinstance(v, list)), []
            )

            for idx, page in enumerate(batch):
                empty = {"new_url": None, "new_title": None, "confidence": 0, "reason": None}
                if idx >= len(gpt_list) or not isinstance(gpt_list[idx], dict):
                    results[page["address"]] = empty
                    continue

                item = gpt_list[idx]
                match_slug = item.get("match_url")
                confidence = int(item.get("confidence", 0))
                reason = item.get("reason")

                if match_slug:
                    candidate_by_slug = {c["match_slug"]: c for c in page.get("_candidates", [])}
                    if match_slug in candidate_by_slug:
                        matched = candidate_by_slug[match_slug]
                        results[page["address"]] = {
                            "new_url": matched["address"],
                            "new_title": matched["title"],
                            "confidence": confidence,
                            "reason": reason,
                        }
                    else:
                        results[page["address"]] = empty
                else:
                    results[page["address"]] = {
                        "new_url": None, "new_title": None,
                        "confidence": 0, "reason": reason,
                    }

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
    Ritorna (results, stats). Results non includono target_domain/target_label.
    """
    new_by_ms: dict[str, dict] = {p["match_slug"]: p for p in new_pages}
    results: list[dict] = []
    to_gpt: list[dict] = []
    stats = {"exact": 0, "slug": 0, "gpt": 0, "no_match": 0}

    for old_page in old_pages:
        ms = old_page["match_slug"]

        # Livello 1 — Match esatto
        if ms in new_by_ms:
            new_page = new_by_ms[ms]
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
            conf = 85
        elif best_overlap >= 0.6 and best_new_page:
            conf = 65
        elif best_overlap >= 0.4 and best_new_page:
            conf = 40
        else:
            best_new_page = None
            conf = 0

        if best_new_page:
            results.append({
                "old_url": old_page["address"],
                "old_title": old_page["title"],
                "old_h1": old_page["h1"],
                "old_inlinks": old_page["inlinks"],
                "new_url": best_new_page["address"],
                "new_title": best_new_page["title"],
                "confidence": conf,
                "match_type": "slug",
                "reason": f"Overlap token slug {int(best_overlap*100)}%",
            })
            stats["slug"] += 1
        else:
            to_gpt.append(old_page)

    # Livello 3 — GPT-4o
    if to_gpt and api_key:
        gpt_results = await _gpt_match_batch(to_gpt, new_pages, api_key)
        for old_page in to_gpt:
            gpt = gpt_results.get(old_page["address"], {})
            new_url = gpt.get("new_url")
            confidence = gpt.get("confidence", 0)
            reason = gpt.get("reason")
            if new_url and confidence > 0:
                results.append({
                    "old_url": old_page["address"],
                    "old_title": old_page["title"],
                    "old_h1": old_page["h1"],
                    "old_inlinks": old_page["inlinks"],
                    "new_url": new_url,
                    "new_title": gpt.get("new_title"),
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

    Multipart form-data:
      config           — JSON: { old_domain, new_domains: [{id, domain, label}], language_rules: [...] }
      old_csv          — CSV Screaming Frog sito vecchio (1 file con tutti gli URL)
      new_csv_{id}     — CSV per ogni new_domain (es. new_csv_uuid-1)

    Matching logic:
      - Se language_rules è vuoto: cerca su tutti i CSV new combinati
      - Se language_rules configurato: routing per regola, fallback su tutti i CSV
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

    old_domain = config.get("old_domain", "").rstrip("/")
    new_domains_cfg = config.get("new_domains", [])   # [{id, domain, label}]
    language_rules = config.get("language_rules", []) # [{pattern, pattern_type, target_domain_id, behavior, ...}]

    # ── Parsing CSV vecchio ──────────────────────────────────────────────────

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

    # ── Parsing CSV nuovi (uno per dominio) ─────────────────────────────────

    # domain_info_by_id: id → {domain, label}
    domain_info_by_id: dict[str, dict] = {}
    # new_pages_by_domain_id: id → lista pagine con match_slug
    new_pages_by_domain_id: dict[str, list[dict]] = {}

    for nd in new_domains_cfg:
        domain_id: str = nd.get("id", "")
        domain_url: str = nd.get("domain", "").rstrip("/")
        domain_label: str = nd.get("label", "")
        domain_info_by_id[domain_id] = {"domain": domain_url, "label": domain_label}

        csv_file = form.get(f"new_csv_{domain_id}")
        if not csv_file or not hasattr(csv_file, "read"):
            continue

        content = await csv_file.read()
        raw_pages = _parse_screaming_frog_csv(content, domain_url)
        new_pages_by_domain_id[domain_id] = [
            {**p, "match_slug": p["address"]} for p in raw_pages
        ]

    # Mappa inversa address → domain_id (per risolvere target dopo il matching)
    url_to_domain_id: dict[str, str] = {}
    for domain_id, pages in new_pages_by_domain_id.items():
        for p in pages:
            url_to_domain_id[p["address"]] = domain_id

    # Pool combinato di tutte le pagine nuove (per matching senza regole o fallback)
    all_new_pages: list[dict] = []
    for pages in new_pages_by_domain_id.values():
        all_new_pages.extend(pages)

    if not all_new_pages:
        raise HTTPException(status_code=400, detail="Nessun CSV sito nuovo valido caricato")

    # ── Helper: annota risultati con target_domain/target_label ─────────────

    def _annotate(r: dict) -> dict:
        new_url = r.get("new_url")
        if new_url:
            did = url_to_domain_id.get(new_url)
            info = domain_info_by_id.get(did, {}) if did else {}
            r["target_domain"] = info.get("domain")
            r["target_label"] = info.get("label")
        else:
            r["target_domain"] = None
            r["target_label"] = None
        return r

    # ── Matching ─────────────────────────────────────────────────────────────

    all_results: list[dict] = []
    all_stats: dict[str, int] = {
        "exact": 0, "slug": 0, "gpt": 0,
        "no_match": 0, "eliminated": 0, "consolidated": 0, "homepage": 0,
    }

    if not language_rules:
        # Nessuna regola: cerca su tutti i CSV combinati
        old_pages = [{**p, "match_slug": p["address"]} for p in old_pages_raw]
        match_results, stats = await _match_pages(old_pages, all_new_pages, x_openai_key)
        for r in match_results:
            _annotate(r)
        all_results = match_results
        for k in ("exact", "slug", "gpt", "no_match"):
            all_stats[k] += stats.get(k, 0)

    else:
        # Routing per regola
        # Step 1: assegna ogni old page a una regola (prima che matcha vince) o fallback
        rule_buckets: dict[int, list[dict]] = {}
        fallback_bucket: list[dict] = []

        for p in old_pages_raw:
            matched_idx = next(
                (i for i, rule in enumerate(language_rules) if _url_matches_rule(p["address"], rule)),
                None,
            )
            if matched_idx is not None:
                rule_buckets.setdefault(matched_idx, []).append(p)
            else:
                fallback_bucket.append(p)

        # Step 2: processa ogni regola
        for rule_idx, rule in enumerate(language_rules):
            rule_pages = rule_buckets.get(rule_idx, [])
            if not rule_pages:
                continue

            behavior: str = rule.get("behavior", "redirect")
            pattern: str = rule.get("pattern", "")
            pattern_type: str = rule.get("pattern_type", "subdirectory")
            target_domain_id: str = rule.get("target_domain_id", "")
            consolidated_domain_id: str = rule.get("consolidated_target_domain_id", "")

            # Calcola match_slug: strip prefisso subdirectory per old pages
            if pattern_type == "subdirectory" and pattern:
                old_pages_with_ms = [
                    {**p, "match_slug": _strip_lang_prefix(p["address"], pattern)}
                    for p in rule_pages
                ]
            else:
                old_pages_with_ms = [{**p, "match_slug": p["address"]} for p in rule_pages]

            # Lingua eliminata
            if behavior == "eliminated":
                for op in old_pages_with_ms:
                    all_results.append({
                        "old_url": op["address"],
                        "old_title": op["title"],
                        "old_h1": op["h1"],
                        "old_inlinks": op["inlinks"],
                        "new_url": None,
                        "new_title": None,
                        "target_domain": None,
                        "target_label": None,
                        "confidence": 0,
                        "match_type": "eliminated",
                        "reason": "Lingua eliminata — nessun redirect",
                    })
                all_stats["eliminated"] += len(rule_pages)
                continue

            # Determina quale pool di pagine nuove usare
            effective_id = consolidated_domain_id if behavior == "consolidated" else target_domain_id
            target_new_pages = new_pages_by_domain_id.get(effective_id, [])
            target_info = domain_info_by_id.get(effective_id, {})

            if not target_new_pages:
                for op in old_pages_with_ms:
                    all_results.append({
                        "old_url": op["address"],
                        "old_title": op["title"],
                        "old_h1": op["h1"],
                        "old_inlinks": op["inlinks"],
                        "new_url": None,
                        "new_title": None,
                        "target_domain": None,
                        "target_label": None,
                        "confidence": 0,
                        "match_type": "no_match",
                        "reason": "Nessun CSV per il dominio di destinazione",
                    })
                all_stats["no_match"] += len(rule_pages)
                continue

            match_results, rule_stats = await _match_pages(
                old_pages_with_ms, target_new_pages, x_openai_key
            )

            rule_matched = rule_stats["exact"] + rule_stats["slug"] + rule_stats["gpt"]
            for r in match_results:
                r["target_domain"] = target_info.get("domain") if r.get("new_url") else None
                r["target_label"] = target_info.get("label") if r.get("new_url") else None
                if behavior == "consolidated" and r["match_type"] not in ("no_match",):
                    r["match_type"] = "consolidated"

            all_results.extend(match_results)

            if behavior == "consolidated":
                all_stats["consolidated"] += rule_matched
            else:
                for k in ("exact", "slug", "gpt"):
                    all_stats[k] += rule_stats.get(k, 0)
            all_stats["no_match"] += rule_stats.get("no_match", 0)

        # Step 3: fallback — pagine senza regola → cerca su tutti i CSV combinati
        if fallback_bucket:
            fb_pages = [{**p, "match_slug": p["address"]} for p in fallback_bucket]
            fb_results, fb_stats = await _match_pages(fb_pages, all_new_pages, x_openai_key)
            for r in fb_results:
                _annotate(r)
            all_results.extend(fb_results)
            for k in ("exact", "slug", "gpt", "no_match"):
                all_stats[k] += fb_stats.get(k, 0)

    # ── Homepage fallback — converte no_match in redirect alla homepage ──────
    fallback_url = None
    fallback_domain = None
    fallback_label = None
    if new_domains_cfg:
        base = new_domains_cfg[0].get("domain", "").rstrip("/")
        if base:
            fallback_url = base + "/"
            fallback_domain = base
            fallback_label = new_domains_cfg[0].get("label", "")

    if fallback_url:
        for r in all_results:
            if r.get("match_type") == "no_match":
                r["new_url"]    = fallback_url
                r["match_type"] = "homepage"
                r["reason"]     = "Nessuna corrispondenza — redirect alla homepage"
                r["target_domain"] = fallback_domain
                r["target_label"]  = fallback_label

    all_stats["homepage"]  = sum(1 for r in all_results if r.get("match_type") == "homepage")
    all_stats["no_match"]  = sum(1 for r in all_results if r.get("match_type") == "no_match")

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
        "homepage": all_stats["homepage"],
        "results": all_results,
        "stats": all_stats,
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
    target_domain e target_label sono per-risultato.
    """
    old_domain = data.old_domain.rstrip("/")

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "URL vecchio (completo)",
        "URL nuovo (completo)",
        "Dominio nuovo",
        "Label dominio",
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
        new_domain_clean = (r.target_domain or "").rstrip("/")
        new_full = (new_domain_clean + r.new_url) if r.new_url else ""

        match_type_label = {
            "exact":        "Esatto",
            "slug":         "Slug",
            "gpt":          "GPT",
            "no_match":     "Nessuno",
            "eliminated":   "Eliminata",
            "consolidated": "Consolidata",
            "homepage":     "Homepage fallback",
        }.get(r.match_type, r.match_type)

        reason = r.reason or (
            "Lingua eliminata — pagina rimossa" if r.match_type == "eliminated" else ""
        )

        writer.writerow([
            old_full,
            new_full,
            r.target_domain or "",
            r.target_label or "",
            r.confidence,
            match_type_label,
            reason,
            r.old_title,
            r.new_title or "",
            r.old_h1,
            "",   # H1 nuovo non nel modello
            r.old_inlinks,
        ])

    csv_bytes = output.getvalue().encode("utf-8-sig")
    return StreamingResponse(
        io.BytesIO(csv_bytes),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=migration_mapping.csv"},
    )
