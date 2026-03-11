from fastapi import APIRouter, HTTPException, Depends, Header
from pydantic import BaseModel
from typing import Optional
from database import supabase
from services.openai_service import generate_article
from auth import get_current_user

router = APIRouter()

# ══════════════════════════════════════════════
#  MODELLI
# ══════════════════════════════════════════════

class ArticleRequest(BaseModel):
    brief_id: Optional[str] = None        # se vuoi caricare il brief da Supabase
    brief_text: Optional[str] = None      # oppure incollarlo direttamente
    client_id: Optional[str] = None       # se presente, carica profilo cliente per contesto
    brand_name: Optional[str] = ""
    target_page_url: Optional[str] = ""
    length: Optional[str] = "Long form"   # Standard | Long form | Authority guide
    creativity: Optional[float] = 0.35

# ══════════════════════════════════════════════
#  ROUTE REDATTORE
# ══════════════════════════════════════════════

@router.post("/generate")
async def generate(
    data: ArticleRequest,
    _user=Depends(get_current_user),
    x_openai_key: Optional[str] = Header(default=None),
):
    """
    Genera un articolo SEO completo a partire da un brief.
    Il brief può essere passato come testo o caricato da Supabase tramite brief_id.
    """
    brief_text = data.brief_text
    brief_record = None

    # Carica il brief da Supabase se è stato passato l'ID
    if data.brief_id and not brief_text:
        res = supabase.table("briefs").select("*").eq("id", data.brief_id).single().execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Brief non trovato")
        brief_record = res.data
        brief_text = brief_record.get("brief_output", "")

    if not brief_text:
        raise HTTPException(status_code=400, detail="Devi fornire un brief_id o un brief_text")

    # Risolvi client_id: da request → dal brief record
    resolved_client_id = data.client_id or (
        brief_record.get("client_id") if brief_record else None
    )

    # Carica profilo cliente completo se disponibile
    tone_of_voice     = ""
    products_services = ""
    usp               = ""
    client_notes      = ""
    brand_name        = data.brand_name or ""

    if resolved_client_id:
        client_res = supabase.table("clients") \
            .select("name, tone_of_voice, products_services, usp, notes") \
            .eq("id", resolved_client_id) \
            .single() \
            .execute()
        if client_res.data:
            tone_of_voice     = client_res.data.get("tone_of_voice") or ""
            products_services = client_res.data.get("products_services") or ""
            usp               = client_res.data.get("usp") or ""
            client_notes      = client_res.data.get("notes") or ""
            if not brand_name:
                brand_name = client_res.data.get("name") or ""

    # Genera l'articolo
    article = await generate_article(
        brief_text=brief_text,
        brand_name=brand_name,
        target_page_url=data.target_page_url,
        length=data.length,
        creativity=data.creativity,
        tone_of_voice=tone_of_voice,
        products_services=products_services,
        usp=usp,
        client_notes=client_notes,
        api_key=x_openai_key,
    )

    # Salva l'articolo nel brief su Supabase
    if data.brief_id:
        supabase.table("briefs").update({"article_output": article}).eq("id", data.brief_id).execute()

    return {
        "brief_id": data.brief_id,
        "article": article,
    }


# ══════════════════════════════════════════════
#  LISTA CLIENTI (per selettore nel redattore)
# ══════════════════════════════════════════════

@router.get("/clients")
def get_clients_for_writer(_user=Depends(get_current_user)):
    """Restituisce id + name di tutti i clienti, ordinati per nome."""
    res = supabase.table("clients") \
        .select("id, name") \
        .order("name") \
        .execute()
    return res.data or []


# ══════════════════════════════════════════════
#  GESTIONE ARTICOLI
# ══════════════════════════════════════════════

@router.get("/articles")
def get_articles(
    client_id: Optional[str] = None,
    _user=Depends(get_current_user),
):
    """Lista tutti i brief con article_output non null."""
    query = supabase.table("briefs") \
        .select("id, keyword, market, intent, created_at, client_id, article_output") \
        .not_.is_("article_output", "null")
    if client_id:
        query = query.eq("client_id", client_id)
    res = query.order("created_at", desc=True).limit(100).execute()
    return res.data or []


class ArticleUpdateRequest(BaseModel):
    article_output: str


@router.patch("/articles/{brief_id}")
def update_article(
    brief_id: str,
    data: ArticleUpdateRequest,
    _user=Depends(get_current_user),
):
    """Aggiorna il testo dell'articolo."""
    res = supabase.table("briefs") \
        .update({"article_output": data.article_output}) \
        .eq("id", brief_id) \
        .execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Articolo non trovato")
    return res.data[0]


@router.delete("/articles/{brief_id}")
def delete_article(
    brief_id: str,
    _user=Depends(get_current_user),
):
    """Azzera article_output senza eliminare il brief."""
    supabase.table("briefs") \
        .update({"article_output": None}) \
        .eq("id", brief_id) \
        .execute()
    return {"deleted": brief_id}
