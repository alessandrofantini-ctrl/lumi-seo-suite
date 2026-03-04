"""
Tests per il comportamento del GSC sync (ADR-002).

Il sync GSC deve aggiornare SOLO le keyword già presenti in keyword_history.
Le query GSC sconosciute devono essere ignorate (non inserite nel DB).

Questi test verificano la logica pura estratta dall'endpoint
routers/clients.py → POST /{client_id}/gsc-sync
"""


def simulate_gsc_sync(gsc_rows: list[dict], existing_map: dict[str, str]) -> dict[str, dict]:
    """
    Riproduce la logica core del GSC sync senza dipendenze da Supabase/FastAPI.

    Args:
        gsc_rows: righe restituite da GSC (query, impressions, clicks, position, ctr)
        existing_map: { keyword.lower() → keyword_id } delle keyword già in DB

    Returns:
        { keyword_id → dati_aggiornati } per le keyword matchate
    """
    updates = {}
    for row in gsc_rows:
        query = row["query"]
        if query.lower() not in existing_map:
            continue  # ADR-002: ignora query non in lista
        keyword_id = existing_map[query.lower()]
        updates[keyword_id] = {
            "impressions": row["impressions"],
            "clicks":      row["clicks"],
            "position":    row["position"],
            "ctr":         row["ctr"],
        }
    return updates


# ── Test core behavior (ADR-002) ──────────────────────────────────────────────

def test_aggiorna_keyword_esistenti():
    existing_map = {"seo agenzia": "id-1", "content marketing": "id-2"}
    gsc_rows = [
        {"query": "seo agenzia",      "impressions": 100, "clicks": 5, "position": 3.2, "ctr": 0.05},
        {"query": "content marketing", "impressions": 50,  "clicks": 2, "position": 7.1, "ctr": 0.04},
    ]
    updates = simulate_gsc_sync(gsc_rows, existing_map)

    assert "id-1" in updates
    assert updates["id-1"]["impressions"] == 100
    assert updates["id-1"]["clicks"]      == 5

    assert "id-2" in updates
    assert updates["id-2"]["position"]    == 7.1


def test_ignora_query_nuove_da_gsc():
    """Comportamento chiave ADR-002: le nuove query GSC NON devono essere aggiunte."""
    existing_map = {"seo agenzia": "id-1"}
    gsc_rows = [
        {"query": "parola non in lista", "impressions": 999, "clicks": 50, "position": 1.0, "ctr": 0.1},
        {"query": "altra query nuova",   "impressions": 500, "clicks": 20, "position": 2.0, "ctr": 0.04},
    ]
    updates = simulate_gsc_sync(gsc_rows, existing_map)

    assert len(updates) == 0, "Nessuna nuova keyword deve essere aggiunta dal GSC sync"


def test_match_case_insensitive():
    """GSC restituisce spesso query in minuscolo — il match deve essere case-insensitive."""
    existing_map = {"seo agenzia": "id-1"}
    gsc_rows = [
        {"query": "SEO Agenzia", "impressions": 100, "clicks": 5, "position": 3.2, "ctr": 0.05},
    ]
    updates = simulate_gsc_sync(gsc_rows, existing_map)

    assert "id-1" in updates


def test_match_parziale_lista():
    """Solo le keyword con match GSC vengono aggiornate; le altre restano invariate."""
    existing_map = {"seo agenzia": "id-1", "content marketing": "id-2"}
    gsc_rows = [
        {"query": "seo agenzia", "impressions": 100, "clicks": 5, "position": 3.2, "ctr": 0.05},
        # "content marketing" non appare su GSC in questo periodo
    ]
    updates = simulate_gsc_sync(gsc_rows, existing_map)

    assert len(updates) == 1
    assert "id-1" in updates
    assert "id-2" not in updates  # nessun dato GSC per questa keyword


def test_lista_vuota():
    updates = simulate_gsc_sync([], {"seo agenzia": "id-1"})
    assert updates == {}


def test_existing_map_vuoto():
    gsc_rows = [
        {"query": "seo agenzia", "impressions": 100, "clicks": 5, "position": 3.2, "ctr": 0.05},
    ]
    updates = simulate_gsc_sync(gsc_rows, {})
    assert updates == {}


def test_metriche_mappate_correttamente():
    existing_map = {"keyword test": "id-99"}
    gsc_rows = [
        {"query": "keyword test", "impressions": 1234, "clicks": 56, "position": 4.7, "ctr": 0.045},
    ]
    updates = simulate_gsc_sync(gsc_rows, existing_map)

    data = updates["id-99"]
    assert data["impressions"] == 1234
    assert data["clicks"]      == 56
    assert data["position"]    == 4.7
    assert data["ctr"]         == 0.045
