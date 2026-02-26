from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from db.supabase import supabase
from services.scraper import scrape_client_deep
from services.openai_service import generate_profile_from_url

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

class AutoGenerateRequest(BaseModel):
    url: str
    openai_api_key: str

class KeywordRequest(BaseModel):
    keyword: str

# ══════════════════════════════════════════════
#  ROUTE CLIENTI
# ══════════════════════════════════════════════

@router.get("/")
def get_all_clients():
    """Restituisce tutti i clienti."""
    res = supabase.table("clients").select("*").order("name").execute()
    return res.data


@router.get("/{client_id}")
def get_client(client_id: str):
    """Restituisce un singolo cliente con il suo storico keyword."""
    client = supabase.table("clients").select("*").eq("id", client_id).single().execute()
    if not client.data:
        raise HTTPException(status_code=404, detail="Cliente non trovato")

    keywords = (
        supabase.table("keyword_history")
        .select("*")
        .eq("client_id", client_id)
        .order("created_at", desc=True)
        .limit(50)
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
def create_client(data: ClientCreate):
    """Crea un nuovo cliente."""
    existing = supabase.table("clients").select("id").eq("name", data.name).execute()
    if existing.data:
        raise HTTPException(status_code=409, detail=f"Cliente '{data.name}' già esistente")

    res = supabase.table("clients").insert(data.model_dump()).execute()
    return res.data[0]


@router.put("/{client_id}")
def update_client(client_id: str, data: ClientUpdate):
    """Aggiorna un cliente esistente."""
    payload = {k: v for k, v in data.model_dump().items() if v is not None}
    payload["updated_at"] = datetime.now().isoformat()

    res = supabase.table("clients").update(payload).eq("id", client_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Cliente non trovato")
    return res.data[0]


@router.delete("/{client_id}")
def delete_client(client_id: str):
    """Elimina un cliente e tutto il suo storico (cascade)."""
    supabase.table("clients").delete().eq("id", client_id).execute()
    return {"deleted": True}


# ══════════════════════════════════════════════
#  AUTO-GENERAZIONE PROFILO DA URL
# ══════════════════════════════════════════════

@router.post("/auto-generate")
async def auto_generate_profile(data: AutoGenerateRequest):
    """Scrapa il sito e genera automaticamente il profilo cliente con GPT."""
    pages_data = scrape_client_deep(data.url, keyword="", max_pages=6)
    if not pages_data:
        raise HTTPException(status_code=422, detail="Impossibile leggere il sito. Prova a inserire i dati manualmente.")

    profile = await generate_profile_from_url(data.url, pages_data, data.openai_api_key)
    return profile


# ══════════════════════════════════════════════
#  STORICO KEYWORD
# ══════════════════════════════════════════════

@router.post("/{client_id}/keywords")
def add_keyword(client_id: str, data: KeywordRequest):
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
    return res.data[0]


@router.delete("/{client_id}/keywords/{keyword_id}")
def delete_keyword(client_id: str, keyword_id: str):
    """Rimuove una keyword dallo storico."""
    supabase.table("keyword_history").delete().eq("id", keyword_id).execute()
    return {"deleted": True}


@router.delete("/{client_id}/keywords")
def clear_keywords(client_id: str):
    """Svuota lo storico keyword di un cliente."""
    supabase.table("keyword_history").delete().eq("client_id", client_id).execute()
    return {"cleared": True}
