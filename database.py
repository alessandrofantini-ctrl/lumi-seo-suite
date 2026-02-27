import os
from supabase import create_client, Client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SECRET_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError(
        "Variabili d'ambiente SUPABASE_URL e SUPABASE_SECRET_KEY mancanti. "
        "Configurale su Render in Environment → Environment Variables."
    )

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
