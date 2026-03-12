from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from database import supabase
from auth import require_admin

router = APIRouter()


# ══════════════════════════════════════════════
#  MODELLI
# ══════════════════════════════════════════════

class CreateUserRequest(BaseModel):
    email: str
    password: str
    full_name: Optional[str] = ""
    role: str = "specialist"  # "admin" | "specialist"


class UpdateUserRequest(BaseModel):
    role: Optional[str] = None
    full_name: Optional[str] = None


class AssignClientRequest(BaseModel):
    assigned_to: Optional[str] = None  # user_id dello specialist (None = rimuovi assegnazione)


# ══════════════════════════════════════════════
#  GESTIONE UTENTI
# ══════════════════════════════════════════════

@router.get("/users")
def list_users(_admin=Depends(require_admin)):
    """Restituisce tutti gli utenti (profili)."""
    res = supabase.table("user_profiles").select("*").order("created_at").execute()
    return res.data or []


@router.post("/users")
def create_user(data: CreateUserRequest, _admin=Depends(require_admin)):
    """Crea un nuovo utente tramite Supabase Auth Admin."""
    if data.role not in ("admin", "specialist"):
        raise HTTPException(status_code=400, detail="Ruolo non valido. Usa 'admin' o 'specialist'")

    # Crea utente in Supabase Auth
    try:
        auth_res = supabase.auth.admin.create_user({
            "email": data.email,
            "password": data.password,
            "email_confirm": True,
        })
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Errore creazione utente: {str(e)}")

    user_id = auth_res.user.id

    # Crea profilo in user_profiles
    profile_res = supabase.table("user_profiles").insert({
        "id": user_id,
        "email": data.email,
        "full_name": data.full_name or "",
        "role": data.role,
    }).execute()

    return profile_res.data[0]


@router.patch("/users/{user_id}")
def update_user(user_id: str, data: UpdateUserRequest, _admin=Depends(require_admin)):
    """Aggiorna ruolo e/o nome di un utente."""
    payload = {}
    if data.role is not None:
        if data.role not in ("admin", "specialist"):
            raise HTTPException(status_code=400, detail="Ruolo non valido")
        payload["role"] = data.role
    if data.full_name is not None:
        payload["full_name"] = data.full_name

    if not payload:
        raise HTTPException(status_code=400, detail="Nessun campo da aggiornare")

    res = supabase.table("user_profiles").update(payload).eq("id", user_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Utente non trovato")
    return res.data[0]


@router.delete("/users/{user_id}")
def delete_user(user_id: str, _admin=Depends(require_admin)):
    """Elimina un utente (profilo + auth)."""
    # Rimuovi profilo (cascade eliminerà anche le ref)
    supabase.table("user_profiles").delete().eq("id", user_id).execute()
    # Elimina da Supabase Auth
    try:
        supabase.auth.admin.delete_user(user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore eliminazione auth: {str(e)}")
    return {"deleted": user_id}


# ══════════════════════════════════════════════
#  GESTIONE ASSEGNAZIONI CLIENTI
# ══════════════════════════════════════════════

@router.get("/clients")
def list_clients_admin(_admin=Depends(require_admin)):
    """Restituisce tutti i clienti con info owner e assigned_to."""
    clients_res = supabase.table("clients") \
        .select("id, name, owner_id, assigned_to") \
        .order("name") \
        .execute()
    clients = clients_res.data or []

    # Carica profili per arricchire owner/assigned con nome/email
    profiles_res = supabase.table("user_profiles").select("id, email, full_name").execute()
    profiles = {p["id"]: p for p in (profiles_res.data or [])}

    result = []
    for c in clients:
        owner = profiles.get(c.get("owner_id") or "")
        assigned = profiles.get(c.get("assigned_to") or "")
        result.append({
            **c,
            "owner": owner,
            "assigned": assigned,
        })
    return result


@router.patch("/clients/{client_id}/assign")
def assign_client(client_id: str, data: AssignClientRequest, _admin=Depends(require_admin)):
    """Assegna (o rimuove l'assegnazione di) un cliente a uno specialist."""
    res = supabase.table("clients") \
        .update({"assigned_to": data.assigned_to}) \
        .eq("id", client_id) \
        .execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Cliente non trovato")
    return res.data[0]
