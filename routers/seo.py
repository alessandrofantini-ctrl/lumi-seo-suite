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
