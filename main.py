from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import clients, seo, writer

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
    allow_headers=["Authorization", "Content-Type", "X-OpenAI-Key", "X-SerpAPI-Key"],
)

app.include_router(clients.router, prefix="/api/clients", tags=["Clienti"])
app.include_router(seo.router,     prefix="/api/seo",     tags=["Analisi SEO"])
app.include_router(writer.router,  prefix="/api/writer",  tags=["Redattore"])

@app.get("/")
def root():
    return {"status": "ok", "service": "Lumi SEO Suite API"}
