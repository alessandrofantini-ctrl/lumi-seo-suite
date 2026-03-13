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


def get_current_user_profile(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Come get_current_user, ma arricchisce con il profilo (role) da Supabase.
    Ritorna: { id, user_id, email, role }
    Il campo 'role' viene letto dalla tabella 'profiles'.
    Default: 'specialist' se il profilo non esiste o il campo è assente.
    """
    try:
        response = supabase.auth.get_user(credentials.credentials)
        if not response.user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token non valido")
        uid = response.user.id
        profile_res = supabase.table("profiles").select("role").eq("id", uid).single().execute()
        role = (profile_res.data or {}).get("role", "specialist") or "specialist"
        return {"id": uid, "user_id": uid, "email": response.user.email, "role": role}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token non valido o scaduto",
            headers={"WWW-Authenticate": "Bearer"},
        )
