"""
Script per creare il primo utente admin.

Uso:
    cd /path/to/lumi-seo-suite
    SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... python scripts/create_first_admin.py

Variabili d'ambiente richieste:
    SUPABASE_URL              → URL progetto Supabase
    SUPABASE_SERVICE_ROLE_KEY → service role key Supabase
    ADMIN_EMAIL               → email del primo admin
    ADMIN_PASSWORD            → password del primo admin
    ADMIN_NAME                → nome completo (opzionale, default "Admin")
"""

import os
import sys

# Aggiungi la root del progetto al path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import supabase

ADMIN_EMAIL    = os.getenv("ADMIN_EMAIL")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
ADMIN_NAME     = os.getenv("ADMIN_NAME", "Admin")

if not ADMIN_EMAIL or not ADMIN_PASSWORD:
    print("Errore: ADMIN_EMAIL e ADMIN_PASSWORD sono obbligatori.")
    print("Esempio: ADMIN_EMAIL=admin@example.com ADMIN_PASSWORD=secret123 python scripts/create_first_admin.py")
    sys.exit(1)

print(f"Creazione admin: {ADMIN_EMAIL} ({ADMIN_NAME})")

# 1. Crea utente in Supabase Auth
try:
    auth_res = supabase.auth.admin.create_user({
        "email": ADMIN_EMAIL,
        "password": ADMIN_PASSWORD,
        "email_confirm": True,
    })
    user_id = auth_res.user.id
    print(f"Utente Auth creato: {user_id}")
except Exception as e:
    print(f"Errore creazione utente Auth: {e}")
    sys.exit(1)

# 2. Crea profilo con ruolo admin
try:
    profile_res = supabase.table("user_profiles").insert({
        "id": user_id,
        "email": ADMIN_EMAIL,
        "full_name": ADMIN_NAME,
        "role": "admin",
    }).execute()
    print(f"Profilo admin creato: {profile_res.data[0]}")
except Exception as e:
    print(f"Errore creazione profilo: {e}")
    sys.exit(1)

print("\nAdmin creato con successo!")
print(f"  Email: {ADMIN_EMAIL}")
print(f"  Nome:  {ADMIN_NAME}")
print(f"  Ruolo: admin")
