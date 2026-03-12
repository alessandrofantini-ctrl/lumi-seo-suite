from fastapi import APIRouter, Depends
from auth import get_current_user_profile

router = APIRouter()


@router.get("/me")
def get_me(profile=Depends(get_current_user_profile)):
    """Restituisce il profilo dell'utente autenticato corrente."""
    return profile
