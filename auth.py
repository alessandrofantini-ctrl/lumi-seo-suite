from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from database import supabase

security = HTTPBearer()


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Verifica il JWT Supabase tramite l'SDK (supporta HS256 ed ES256)."""
    try:
        response = supabase.auth.get_user(credentials.credentials)
        if not response.user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token non valido")
        return {"user_id": response.user.id, "email": response.user.email}
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token non valido o scaduto",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_current_user_profile(user=Depends(get_current_user)) -> dict:
    """Ritorna user + profilo con ruolo (specialist di default se profilo assente)."""
    res = supabase.table("user_profiles") \
        .select("*") \
        .eq("id", user["user_id"]) \
        .single() \
        .execute()
    profile = res.data or {}
    return {
        "id":        user["user_id"],
        "email":     user["email"],
        "role":      profile.get("role", "specialist"),
        "full_name": profile.get("full_name", ""),
    }


def require_admin(profile=Depends(get_current_user_profile)) -> dict:
    """Dependency che blocca se l'utente non è admin."""
    if profile["role"] != "admin":
        raise HTTPException(
            status_code=403,
            detail="Accesso riservato agli amministratori",
        )
    return profile
