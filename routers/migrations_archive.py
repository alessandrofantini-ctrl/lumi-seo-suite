from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from database import supabase
from auth import get_current_user

router = APIRouter()

# ══════════════════════════════════════════════
#  MODELLI
# ══════════════════════════════════════════════

class MigrationSaveRequest(BaseModel):
    name: str
    old_domain: str
    new_domains: list
    results: list
    total_urls: int
    matched_urls: int


# ══════════════════════════════════════════════
#  ENDPOINT
# ══════════════════════════════════════════════

@router.post("/")
def save_migration(
    data: MigrationSaveRequest,
    _user=Depends(get_current_user),
):
    res = supabase.table("migrations").insert({
        "name":         data.name,
        "old_domain":   data.old_domain,
        "new_domains":  data.new_domains,
        "results":      data.results,
        "total_urls":   data.total_urls,
        "matched_urls": data.matched_urls,
    }).execute()
    if not res.data:
        raise HTTPException(status_code=500, detail="Errore salvataggio")
    return res.data[0]


@router.get("/")
def list_migrations(_user=Depends(get_current_user)):
    res = supabase.table("migrations") \
        .select("id, name, old_domain, new_domains, total_urls, matched_urls, created_at") \
        .order("created_at", desc=True) \
        .execute()
    return res.data or []


@router.get("/{migration_id}")
def get_migration(
    migration_id: str,
    _user=Depends(get_current_user),
):
    res = supabase.table("migrations") \
        .select("*") \
        .eq("id", migration_id) \
        .single() \
        .execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Migrazione non trovata")
    return res.data


@router.delete("/{migration_id}")
def delete_migration(
    migration_id: str,
    _user=Depends(get_current_user),
):
    supabase.table("migrations") \
        .delete() \
        .eq("id", migration_id) \
        .execute()
    return {"deleted": migration_id}
