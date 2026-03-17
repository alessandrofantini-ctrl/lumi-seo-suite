import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import clients, seo, writer, migration, migrations_archive, dashboard, admin, auth_router, meta

# DataForSEO — credenziali lato server (non via header HTTP)
# Impostare come variabili d'ambiente in Render:
#   DATAFORSEO_LOGIN    → login account DataForSEO
#   DATAFORSEO_PASSWORD → password account DataForSEO
DATAFORSEO_LOGIN    = os.getenv("DATAFORSEO_LOGIN", "")
DATAFORSEO_PASSWORD = os.getenv("DATAFORSEO_PASSWORD", "")

app = FastAPI(
    title="Lumi SEO Suite API",
    description="Backend per la SEO Suite di Lumi Company",
    version="1.0.0"
)

# CORS — permette al frontend Vercel di chiamare questo backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # in produzione sostituire con l'URL Vercel
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(clients.router,             prefix="/api/clients",    tags=["Clienti"])
app.include_router(seo.router,                 prefix="/api/seo",        tags=["Analisi SEO"])
app.include_router(writer.router,              prefix="/api/writer",     tags=["Redattore"])
app.include_router(migration.router,           prefix="/api/migration",  tags=["Migrazione"])
app.include_router(migrations_archive.router,  prefix="/api/migrations", tags=["Migrazioni archiviate"])
app.include_router(dashboard.router,           prefix="/api/dashboard",  tags=["Dashboard"])
app.include_router(admin.router,               prefix="/api/admin",      tags=["Admin"])
app.include_router(auth_router.router,         prefix="/api/auth",       tags=["Auth"])
app.include_router(meta.router,                prefix="/api/meta",       tags=["Meta Generator"])

@app.get("/")
def root():
    return {"status": "ok", "service": "Lumi SEO Suite API"}
