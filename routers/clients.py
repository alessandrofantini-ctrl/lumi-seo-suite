import logging
import os

from fastapi import APIRouter, HTTPException, Depends, Header
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from database import supabase
from services.scraper import scrape_client_deep
from services.openai_service import generate_profile_from_url
from services.dataforseo import get_search_volume
from auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter()

# ══════════════════════════════════════════════
#  MODELLI
# ══════════════════════════════════════════════

class ClientCreate(BaseModel):
    name: str
    url: Optional[str] = ""
    sector: Optional[str] = ""
    brand_name: Optional[str] = ""
    tone_of_voice: Optional[str] = "Autorevole & tecnico"
    usp: Optional[str] = ""
    products_services: Optional[str] = ""
    target_audience: Optional[str] = ""
    geo: Optional[str] = ""
    notes: Optional[str] = ""
    gsc_property: Optional[str] = ""
    language_code: Optional[str] = "it"
    location_code: Optional[int] = 2380

class ClientUpdate(BaseModel):
    url: Optional[str] = None
    sector: Optional[str] = None
    brand_name: Optional[str] = None
    tone_of_voice: Optional[str] = None
    usp: Optional[str] = None
    products_services: Optional[str] = None
    target_audience: Optional[str] = None
    geo: Optional[str] = None
    notes: Optional[str] = None
    gsc_property: Optional[str] = None
    language_code: Optional[str] = None
    location_code: Optional[int] = None

class AutoGenerateRequest(BaseModel):
    url: str

class KeywordRequest(BaseModel):
    keyword: str

class KeywordBulkRequest(BaseModel):
    keywords: list[str]

class KeywordUpdate(BaseModel):
    status:   Optional[str] = None  # backlog | planned | brief_done | written | published
    cluster:  Optional[str] = None
    intent:   Optional[str] = None  # informativo | commerciale | navigazionale | transazionale
    priority: Optional[str] = None  # alta | media | bassa

# ══════════════════════════════════════════════
#  ROUTE CLIENTI
# ══════════════════════════════════════════════

@router.get("/")
def get_all_clients(_user=Depends(get_current_user)):
    """Restituisce tutti i clienti."""
    res = supabase.table("clients").select("*").order("name").execute()
    return res.data


@router.get("/{client_id}")
def get_client(client_id: str, _user=Depends(get_current_user)):
    """Restituisce un singolo cliente con il suo storico keyword."""
    client = supabase.table("clients").select("*").eq("id", client_id).single().execute()
    if not client.data:
        raise HTTPException(status_code=404, detail="Cliente non trovato")

    keywords = (
        supabase.table("keyword_history")
        .select("*")
        .eq("client_id", client_id)
        .order("created_at", desc=True)
        .execute()
    )

    briefs = (
        supabase.table("briefs")
        .select("id, keyword, market, intent, created_at")
        .eq("client_id", client_id)
        .order("created_at", desc=True)
        .limit(20)
        .execute()
    )

    return {
        **client.data,
        "keyword_history": keywords.data,
        "briefs": briefs.data,
    }


@router.post("/")
def create_client(data: ClientCreate, _user=Depends(get_current_user)):
    """Crea un nuovo cliente."""
    existing = supabase.table("clients").select("id").eq("name", data.name).execute()
    if existing.data:
        raise HTTPException(status_code=409, detail=f"Cliente '{data.name}' già esistente")

    res = supabase.table("clients").insert(data.model_dump()).execute()
    return res.data[0]


@router.put("/{client_id}")
def update_client(client_id: str, data: ClientUpdate, _user=Depends(get_current_user)):
    """Aggiorna un cliente esistente."""
    payload = {k: v for k, v in data.model_dump().items() if v is not None}
    payload["updated_at"] = datetime.now().isoformat()

    res = supabase.table("clients").update(payload).eq("id", client_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Cliente non trovato")
    return res.data[0]


@router.delete("/{client_id}")
def delete_client(client_id: str, _user=Depends(get_current_user)):
    """Elimina un cliente e tutto il suo storico (cascade)."""
    supabase.table("clients").delete().eq("id", client_id).execute()
    return {"deleted": True}


# ══════════════════════════════════════════════
#  AUTO-GENERAZIONE PROFILO DA URL
# ══════════════════════════════════════════════

@router.post("/auto-generate")
async def auto_generate_profile(
    data: AutoGenerateRequest,
    _user=Depends(get_current_user),
    x_openai_key: Optional[str] = Header(default=None),
):
    """Scrapa il sito e genera automaticamente il profilo cliente con GPT."""
    pages_data = scrape_client_deep(data.url, keyword="", max_pages=6)
    if not pages_data:
        raise HTTPException(status_code=422, detail="Impossibile leggere il sito. Prova a inserire i dati manualmente.")

    profile = await generate_profile_from_url(data.url, pages_data, api_key=x_openai_key)
    return profile


# ══════════════════════════════════════════════
#  STORICO KEYWORD
# ══════════════════════════════════════════════

@router.post("/{client_id}/keywords")
async def add_keyword(client_id: str, data: KeywordRequest, _user=Depends(get_current_user)):
    """Aggiunge una keyword allo storico del cliente."""
    existing = (
        supabase.table("keyword_history")
        .select("id")
        .eq("client_id", client_id)
        .eq("keyword", data.keyword)
        .execute()
    )
    if existing.data:
        return {"message": "Keyword già presente nello storico"}

    res = supabase.table("keyword_history").insert({
        "client_id": client_id,
        "keyword": data.keyword,
    }).execute()
    record = res.data[0]

    # DataForSEO — arricchimento volume di ricerca (silenzioso se credenziali assenti)
    dfs_login    = os.getenv("DATAFORSEO_LOGIN", "")
    dfs_password = os.getenv("DATAFORSEO_PASSWORD", "")
    if dfs_login and dfs_password:
        try:
            client_row = (
                supabase.table("clients")
                .select("language_code, location_code")
                .eq("id", client_id)
                .single()
                .execute()
            )
            lang = (client_row.data or {}).get("language_code") or "it"
            loc  = (client_row.data or {}).get("location_code") or 2380
            volumes = await get_search_volume([data.keyword], lang, loc, dfs_login, dfs_password)
            vol = volumes.get(data.keyword)
            if vol is not None:
                now = datetime.now().isoformat()
                supabase.table("keyword_history").update({
                    "search_volume": vol,
                    "volume_updated_at": now,
                }).eq("id", record["id"]).execute()
                record["search_volume"] = vol
                record["volume_updated_at"] = now
        except Exception as exc:
            logger.warning("DataForSEO skip (singola): %s", exc)

    return record


@router.post("/{client_id}/keywords/bulk")
async def bulk_add_keywords(client_id: str, data: KeywordBulkRequest, _user=Depends(get_current_user)):
    """Importa una lista di keyword, saltando i duplicati."""
    existing = (
        supabase.table("keyword_history")
        .select("keyword")
        .eq("client_id", client_id)
        .execute()
    )
    existing_set = {r["keyword"].lower() for r in existing.data}

    to_insert = [
        {"client_id": client_id, "keyword": kw.strip(), "status": "backlog"}
        for kw in data.keywords
        if kw.strip() and kw.strip().lower() not in existing_set
    ]

    if not to_insert:
        return {"added": 0, "skipped": len(data.keywords)}

    res = supabase.table("keyword_history").insert(to_insert).execute()
    inserted = res.data

    # DataForSEO — arricchimento volume in batch (silenzioso se credenziali assenti)
    dfs_login    = os.getenv("DATAFORSEO_LOGIN", "")
    dfs_password = os.getenv("DATAFORSEO_PASSWORD", "")
    if dfs_login and dfs_password and inserted:
        try:
            client_row = (
                supabase.table("clients")
                .select("language_code, location_code")
                .eq("id", client_id)
                .single()
                .execute()
            )
            lang = (client_row.data or {}).get("language_code") or "it"
            loc  = (client_row.data or {}).get("location_code") or 2380
            kws = [r["keyword"] for r in inserted]
            volumes = await get_search_volume(kws, lang, loc, dfs_login, dfs_password)
            now = datetime.now().isoformat()
            for record in inserted:
                vol = volumes.get(record["keyword"])
                if vol is not None:
                    supabase.table("keyword_history").update({
                        "search_volume": vol,
                        "volume_updated_at": now,
                    }).eq("id", record["id"]).execute()
        except Exception as exc:
            logger.warning("DataForSEO skip (bulk): %s", exc)

    return {"added": len(inserted), "skipped": len(data.keywords) - len(to_insert)}


@router.patch("/{client_id}/keywords/{keyword_id}")
def update_keyword(client_id: str, keyword_id: str, data: KeywordUpdate, _user=Depends(get_current_user)):
    """Aggiorna status, cluster, intent e/o priority di una keyword."""
    valid_status   = {"backlog", "planned", "brief_done", "written", "published"}
    valid_intent   = {"informativo", "commerciale", "navigazionale", "transazionale"}
    valid_priority = {"alta", "media", "bassa"}

    if data.status   and data.status   not in valid_status:
        raise HTTPException(status_code=400, detail=f"Status non valido. Valori: {valid_status}")
    if data.intent   and data.intent   not in valid_intent:
        raise HTTPException(status_code=400, detail=f"Intent non valido. Valori: {valid_intent}")
    if data.priority and data.priority not in valid_priority:
        raise HTTPException(status_code=400, detail=f"Priority non valida. Valori: {valid_priority}")

    payload = {k: v for k, v in data.model_dump().items() if v is not None}
    if not payload:
        raise HTTPException(status_code=400, detail="Nessun campo da aggiornare")

    res = (
        supabase.table("keyword_history")
        .update(payload)
        .eq("id", keyword_id)
        .eq("client_id", client_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Keyword non trovata")
    return res.data[0]


@router.delete("/{client_id}/keywords/{keyword_id}")
def delete_keyword(client_id: str, keyword_id: str, _user=Depends(get_current_user)):
    """Rimuove una keyword dallo storico."""
    supabase.table("keyword_history").delete().eq("id", keyword_id).execute()
    return {"deleted": True}


@router.delete("/{client_id}/keywords")
def clear_keywords(client_id: str, _user=Depends(get_current_user)):
    """Svuota lo storico keyword di un cliente."""
    supabase.table("keyword_history").delete().eq("client_id", client_id).execute()
    return {"cleared": True}


# ══════════════════════════════════════════════
#  GOOGLE SEARCH CONSOLE SYNC
# ══════════════════════════════════════════════

@router.post("/{client_id}/gsc-sync")
def gsc_sync(client_id: str, _user=Depends(get_current_user)):
    """Sincronizza i dati di Google Search Console per un cliente."""
    from services.gsc import fetch_gsc_queries

    client = supabase.table("clients").select("gsc_property").eq("id", client_id).single().execute()
    if not client.data or not client.data.get("gsc_property"):
        raise HTTPException(
            status_code=400,
            detail="gsc_property non configurata per questo cliente. Inseriscila nel profilo."
        )

    try:
        rows = fetch_gsc_queries(client.data["gsc_property"])
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore GSC: {str(e)}")

    if not rows:
        return {"synced": 0, "total": 0}

    existing = (
        supabase.table("keyword_history")
        .select("id, keyword")
        .eq("client_id", client_id)
        .execute()
    )
    existing_map = {r["keyword"].lower(): r["id"] for r in existing.data}

    now = datetime.now().isoformat()
    updated = 0

    # Recupera i valori attuali di position per storicizzarli in position_prev.
    existing_positions = (
        supabase.table("keyword_history")
        .select("id, position")
        .eq("client_id", client_id)
        .execute()
    )
    position_map: dict[str, float | None] = {r["id"]: r.get("position") for r in existing_positions.data}

    # Aggiorna solo le keyword già presenti — il GSC arricchisce i dati,
    # non importa nuove query. L'elenco target viene gestito manualmente.
    for row in rows:
        query = row["query"]
        if query.lower() not in existing_map:
            continue
        kw_id = existing_map[query.lower()]
        payload: dict = {
            "impressions":    row["impressions"],
            "clicks":         row["clicks"],
            "position":       row["position"],
            "ctr":            row["ctr"],
            "gsc_updated_at": now,
            "position_updated_at": now,
        }
        # Salva la posizione precedente solo se ne esisteva già una.
        prev = position_map.get(kw_id)
        if prev is not None:
            payload["position_prev"] = prev
        supabase.table("keyword_history").update(payload).eq("id", kw_id).execute()
        updated += 1

    return {"synced": updated, "total": len(existing_map)}
