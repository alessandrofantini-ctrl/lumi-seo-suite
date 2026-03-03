from fastapi import APIRouter, HTTPException, Depends
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
    brand_name: Optional[str] = ""
    target_page_url: Optional[str] = ""
    length: Optional[str] = "Long form"   # Standard | Long form | Authority guide
    creativity: Optional[float] = 0.35

# ══════════════════════════════════════════════
#  ROUTE REDATTORE
# ══════════════════════════════════════════════

@router.post("/generate")
async def generate(data: ArticleRequest, _user=Depends(get_current_user)):
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

    # Genera l'articolo
    article = await generate_article(
        brief_text=brief_text,
        brand_name=data.brand_name,
        target_page_url=data.target_page_url,
        length=data.length,
        creativity=data.creativity,
    )

    # Salva l'articolo nel brief su Supabase
    if data.brief_id:
        supabase.table("briefs").update({"article_output": article}).eq("id", data.brief_id).execute()

    return {
        "brief_id": data.brief_id,
        "article": article,
    }
