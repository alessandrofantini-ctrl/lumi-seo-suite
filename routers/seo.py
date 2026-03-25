from fastapi import APIRouter, HTTPException, Depends, Header, BackgroundTasks
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from database import supabase
from services.scraper import scrape_site_content, build_serp_snapshot, aggregate_competitor_insights
from services.serp import get_serp_data
from services.openai_service import generate_seo_brief
from concurrent.futures import ThreadPoolExecutor, as_completed
from auth import get_current_user

router = APIRouter()

# ══════════════════════════════════════════════
#  MERCATI SUPPORTATI
# ══════════════════════════════════════════════

MARKETS = {
    "🇮🇹 Italia":          {"gl": "it", "hl": "it",  "domain": "google.it"},
    "🇺🇸 USA (English)":   {"gl": "us", "hl": "en",  "domain": "google.com"},
    "🇬🇧 UK":              {"gl": "uk", "hl": "en",  "domain": "google.co.uk"},
    "🇪🇸 Spagna":          {"gl": "es", "hl": "es",  "domain": "google.es"},
    "🇫🇷 Francia":         {"gl": "fr", "hl": "fr",  "domain": "google.fr"},
    "🇩🇪 Germania":        {"gl": "de", "hl": "de",  "domain": "google.de"},
}

# ══════════════════════════════════════════════
#  MODELLI
# ══════════════════════════════════════════════

class SeoAnalysisRequest(BaseModel):
    keyword: str
    client_id: Optional[str] = None
    market: Optional[str] = "🇮🇹 Italia"
    intent: Optional[str] = "Informativo"
    max_competitors: Optional[int] = 6
    include_schema: Optional[bool] = True
    save_brief: Optional[bool] = True

# ══════════════════════════════════════════════
#  LOGICA ANALISI — eseguita in background
# ══════════════════════════════════════════════

async def _run_analysis(
    job_id: str,
    data: SeoAnalysisRequest,
    x_openai_key: str | None,
    x_serpapi_key: str | None,
    user_id: str,
):
    """Esegue l'analisi SEO in background e aggiorna il job su Supabase."""
    try:
        # Segna come running
        supabase.table("seo_jobs").update({
            "status":     "running",
            "updated_at": datetime.utcnow().isoformat(),
        }).eq("id", job_id).execute()

        market_params = MARKETS.get(data.market)

        # 1 — SERP
        serp_json = get_serp_data(
            query=data.keyword,
            gl=market_params["gl"],
            hl=market_params["hl"],
            domain=market_params["domain"],
            api_key=x_serpapi_key,
        )
        if not serp_json or "organic_results" not in serp_json:
            raise ValueError("Nessun risultato SERP. Verifica la SerpAPI key.")

        serp_snapshot = build_serp_snapshot(serp_json, data.max_competitors)
        organic_urls = [x["link"] for x in serp_snapshot["organic"] if x.get("link")][:data.max_competitors]

        # 2 — Contesto cliente (se selezionato)
        client_context = ""
        if data.client_id:
            res = supabase.table("clients").select("*").eq("id", data.client_id).single().execute()
            client_data = res.data
            if client_data:
                kw_history = (
                    supabase.table("keyword_history")
                    .select("keyword")
                    .eq("client_id", data.client_id)
                    .order("created_at", desc=True)
                    .limit(20)
                    .execute()
                )
                kw_list = [r["keyword"] for r in kw_history.data]
                products_list = [
                    line.strip()
                    for line in client_data.get("products_services", "").splitlines()
                    if line.strip()
                ]
                client_context = "\n".join([
                    f"Cliente: {client_data.get('name', '')}",
                    f"Settore: {client_data.get('sector', '')}",
                    f"Zona geografica: {client_data.get('geo', '')}",
                    f"Target audience: {client_data.get('target_audience', '')}",
                    f"Prodotti/servizi: {products_list}",
                    f"USP: {client_data.get('usp', '')}",
                    f"Note strategiche: {client_data.get('notes', '')}",
                    f"Keyword già usate: {kw_list}",
                ])

        # 3 — Scraping competitor in parallelo
        competitor_results = []
        with ThreadPoolExecutor(max_workers=6) as ex:
            futures = [ex.submit(scrape_site_content, url, True, data.include_schema) for url in organic_urls]
            for fut in as_completed(futures):
                result = fut.result()
                if result:
                    competitor_results.append(result)

        if not competitor_results:
            raise ValueError("Impossibile leggere i competitor. Riprova.")

        # 4 — Aggrega insight
        agg = aggregate_competitor_insights(competitor_results, market_params["hl"])

        # 5 — Genera brief con GPT-4o
        brief_output = await generate_seo_brief(
            keyword=data.keyword,
            market=data.market,
            market_params=market_params,
            intent=data.intent,
            client_context=client_context,
            serp_snapshot=serp_snapshot,
            competitor_results=competitor_results,
            aggregated=agg,
            api_key=x_openai_key,
        )

        # 6 — Salva su Supabase
        brief_id = None
        if data.save_brief:
            insert_data = {
                "keyword": data.keyword,
                "market": data.market,
                "intent": data.intent,
                "brief_output": brief_output,
            }
            if data.client_id:
                insert_data["client_id"] = data.client_id

            res = supabase.table("briefs").insert(insert_data).execute()
            brief_id = res.data[0]["id"] if res.data else None

            # Aggiunge keyword allo storico
            if data.client_id:
                existing = (
                    supabase.table("keyword_history")
                    .select("id")
                    .eq("client_id", data.client_id)
                    .eq("keyword", data.keyword)
                    .execute()
                )
                if not existing.data:
                    supabase.table("keyword_history").insert({
                        "client_id": data.client_id,
                        "keyword": data.keyword,
                    }).execute()

        # Segna come done con il risultato
        supabase.table("seo_jobs").update({
            "status":     "done",
            "updated_at": datetime.utcnow().isoformat(),
            "result": {
                "brief_id":             brief_id,
                "brief_output":         brief_output,
                "serp_snapshot":        serp_snapshot,
                "competitors_analysed": len(competitor_results),
                "aggregated_insights":  agg,
            },
        }).eq("id", job_id).execute()

    except Exception as e:
        supabase.table("seo_jobs").update({
            "status":     "error",
            "updated_at": datetime.utcnow().isoformat(),
            "error":      str(e),
        }).eq("id", job_id).execute()


# ══════════════════════════════════════════════
#  ROUTE ANALISI SEO
# ══════════════════════════════════════════════

@router.get("/markets")
def get_markets():
    """Restituisce i mercati disponibili."""
    return list(MARKETS.keys())


@router.post("/analyse")
async def analyse(
    data: SeoAnalysisRequest,
    background_tasks: BackgroundTasks,
    user=Depends(get_current_user),
    x_openai_key: Optional[str] = Header(default=None),
    x_serpapi_key: Optional[str] = Header(default=None),
):
    """Crea un job e avvia l'analisi SEO in background."""
    market_params = MARKETS.get(data.market)
    if not market_params:
        raise HTTPException(
            status_code=400,
            detail=f"Mercato '{data.market}' non supportato"
        )

    # Crea il job su Supabase
    job_res = supabase.table("seo_jobs").insert({
        "user_id":   user["user_id"],
        "client_id": data.client_id,
        "keyword":   data.keyword,
        "market":    data.market,
        "intent":    data.intent,
        "status":    "pending",
    }).execute()

    job_id = job_res.data[0]["id"]

    # Avvia elaborazione in background
    background_tasks.add_task(
        _run_analysis,
        job_id=job_id,
        data=data,
        x_openai_key=x_openai_key,
        x_serpapi_key=x_serpapi_key,
        user_id=user["user_id"],
    )

    return {"job_id": job_id, "status": "pending"}


@router.get("/jobs/{job_id}")
def get_job(job_id: str, _user=Depends(get_current_user)):
    """Polling: ritorna lo stato attuale del job."""
    res = supabase.table("seo_jobs") \
        .select("*") \
        .eq("id", job_id) \
        .single() \
        .execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Job non trovato")
    return res.data


@router.get("/jobs")
def list_jobs(_user=Depends(get_current_user)):
    """Lista ultimi 20 job dell'utente corrente."""
    res = supabase.table("seo_jobs") \
        .select("id, keyword, market, intent, status, created_at, updated_at") \
        .eq("user_id", _user["user_id"]) \
        .order("created_at", desc=True) \
        .limit(20) \
        .execute()
    return res.data or []


# ══════════════════════════════════════════════
#  ROUTE BRIEF
# ══════════════════════════════════════════════

@router.post("/batch-brief")
async def batch_brief(
    data: BatchBriefRequest,
    _user=Depends(get_current_user),
    x_openai_key: Optional[str] = Header(default=None),
    x_serpapi_key: Optional[str] = Header(default=None),
):
    """
    Endpoint sincrono: scraping Rexel facets + SERP + competitor scraping +
    calcolo lunghezza + GPT-4o → { h1, lunghezza_consigliata, outline, faq_domande }.
    """
    from services.openai_service import generate_batch_brief
    from services.scraper import scrape_rexel_facets, scrape_competitor_for_brief
    from services.serp import get_serp_data
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # 1 ── Profilo cliente ────────────────────────────────────────────────
    res = supabase.table("clients").select(
        "name, url, tone_of_voice, usp, products_services, notes"
    ).eq("id", data.client_id).single().execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Cliente non trovato")
    cd = res.data

    # 2 ── Rexel facets (silenzioso se non rexel.it o errore) ────────────
    rexel_data = {"brands": [], "filters": []}
    if data.url and "rexel.it" in data.url:
        try:
            rexel_data = scrape_rexel_facets(data.url)
        except Exception:
            pass

    # 3 ── SERP ───────────────────────────────────────────────────────────
    market_params = MARKETS.get(data.market, MARKETS["🇮🇹 Italia"])
    serp_json = get_serp_data(
        query=data.keyword,
        gl=market_params["gl"],
        hl=market_params["hl"],
        domain=market_params["domain"],
        api_key=x_serpapi_key,
    )
    serp_urls: list[str] = []
    if serp_json and "organic_results" in serp_json:
        client_domain = ""
        if cd.get("url"):
            try:
                from urllib.parse import urlparse
                client_domain = urlparse(cd["url"]).netloc.replace("www.", "")
            except Exception:
                pass
        for r in serp_json["organic_results"]:
            link = r.get("link", "")
            if link and (not client_domain or client_domain not in link):
                serp_urls.append(link)
            if len(serp_urls) >= data.max_competitors:
                break

    # Competitor prioritari prima, poi SERP (no duplicati)
    priority_urls = [u for u in data.competitor_urls if u.strip()]
    combined_urls: list[str] = []
    seen_urls: set[str] = set()
    for u in priority_urls + serp_urls:
        if u not in seen_urls:
            seen_urls.add(u)
            combined_urls.append(u)
        if len(combined_urls) >= data.max_competitors:
            break

    # 4 ── Scraping competitor in parallelo ──────────────────────────────
    comp_results: list[dict] = []
    if combined_urls:
        priority_set = set(priority_urls)
        with ThreadPoolExecutor(max_workers=6) as ex:
            future_map = {
                ex.submit(scrape_competitor_for_brief, u): u
                for u in combined_urls
            }
            for fut in as_completed(future_map):
                r = fut.result()
                if r:
                    comp_results.append(r)
        # Ordina: prioritari prima, poi per word_count desc
        comp_results.sort(
            key=lambda x: (0 if x["url"] in priority_set else 1, -x["word_count"])
        )

    # 5 ── Calcolo lunghezza ──────────────────────────────────────────────
    wc_list = [r["word_count"] for r in comp_results if r["word_count"] > 0]
    if wc_list:
        avg_wc      = int(sum(wc_list) / len(wc_list))
        lo          = max(300, int(avg_wc * (1 + (data.margin_pct - 10) / 100)))
        hi          = max(lo + 150, int(avg_wc * (1 + (data.margin_pct + 10) / 100)))
        target_range = f"{lo}–{hi}"
    else:
        avg_wc       = 0
        target_range = data.fallback_range

    # 6 ── Generazione GPT-4o ────────────────────────────────────────────
    try:
        result = await generate_batch_brief(
            keyword=data.keyword,
            market=data.market,
            intent=data.intent,
            client_name=cd.get("name", ""),
            client_url=cd.get("url", ""),
            tone_of_voice=cd.get("tone_of_voice", ""),
            usp=cd.get("usp", ""),
            client_notes=cd.get("notes", ""),
            brands=rexel_data["brands"],
            filters=rexel_data["filters"],
            competitor_block=comp_results,
            avg_wc=avg_wc,
            target_range=target_range,
            max_h2=data.max_h2,
            page_url=data.url or "",
            api_key=x_openai_key,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {
        **result,
        "avg_wc":         avg_wc,
        "target_range":   target_range,
        "brands_count":   len(rexel_data["brands"]),
        "filters_count":  len(rexel_data["filters"]),
    }


@router.get("/briefs/{brief_id}")
def get_brief(brief_id: str, _user=Depends(get_current_user)):
    """Recupera un brief salvato."""
    res = supabase.table("briefs").select("*").eq("id", brief_id).single().execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Brief non trovato")
    return res.data


@router.get("/briefs")
def get_all_briefs(client_id: Optional[str] = None, _user=Depends(get_current_user)):
    """Recupera tutti i brief, opzionalmente filtrati per cliente."""
    query = supabase.table("briefs").select(
        "id, keyword, market, intent, created_at, client_id, brief_output"
    )
    if client_id:
        query = query.eq("client_id", client_id)
    res = query.order("created_at", desc=True).limit(50).execute()
    return res.data


class BatchBriefRequest(BaseModel):
    keyword: str
    market: str
    intent: str
    url: Optional[str] = None
    client_id: str
    competitor_urls: list[str] = []
    max_competitors: int = 5
    margin_pct: int = 20
    fallback_range: str = "550–900"
    max_h2: int = 8


class BriefUpdateRequest(BaseModel):
    brief_output: str


@router.patch("/briefs/{brief_id}")
def update_brief(
    brief_id: str,
    data: BriefUpdateRequest,
    _user=Depends(get_current_user),
):
    """Aggiorna il testo di un brief esistente."""
    res = supabase.table("briefs") \
        .update({"brief_output": data.brief_output}) \
        .eq("id", brief_id) \
        .execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Brief non trovato")
    return res.data[0]


@router.delete("/briefs/{brief_id}")
def delete_brief(
    brief_id: str,
    _user=Depends(get_current_user),
):
    """Elimina un brief."""
    supabase.table("briefs") \
        .delete() \
        .eq("id", brief_id) \
        .execute()
    return {"deleted": brief_id}
