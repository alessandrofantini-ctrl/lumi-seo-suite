from fastapi import APIRouter, Depends
from collections import defaultdict
from database import supabase
from auth import get_current_user_profile

router = APIRouter()


@router.get("/")
async def get_dashboard(profile=Depends(get_current_user_profile)):
    """
    Ritorna metriche aggregate per ogni cliente:
    - keywords_crescita: keyword con position < position_prev
    - keywords_calo: keyword con position > position_prev
    - last_sync: data piu recente tra i gsc_updated_at del cliente
    - total_keywords: totale keyword

    Ordine: keywords_calo desc (clienti piu critici prima).
    Admin: vede tutti i clienti.
    Specialist: vede solo i clienti di cui e owner o assigned_to.
    """
    clients_res = supabase.table("clients") \
        .select("id, name, sector, owner_id, assigned_to") \
        .execute()
    all_clients = clients_res.data or []

    # Specialist: filtra solo clienti propri o assegnati
    if profile["role"] != "admin":
        uid = profile["id"]
        all_clients = [
            c for c in all_clients
            if c.get("owner_id") == uid
            or c.get("assigned_to") == uid
        ]

    if not all_clients:
        return []

    kw_res = (
        supabase.table("keyword_history")
        .select("client_id, position, position_prev, gsc_updated_at")
        .execute()
    )
    kw_rows = kw_res.data or []

    # Aggrega per cliente
    stats: dict[str, dict] = defaultdict(lambda: {
        "total": 0,
        "crescita": 0,
        "calo": 0,
        "last_sync": None,
    })

    for row in kw_rows:
        cid = row["client_id"]
        stats[cid]["total"] += 1

        pos      = row.get("position")
        pos_prev = row.get("position_prev")

        if pos is not None and pos_prev is not None:
            if pos < pos_prev:
                stats[cid]["crescita"] += 1
            elif pos > pos_prev:
                stats[cid]["calo"] += 1

        gsc_date = row.get("gsc_updated_at")
        if gsc_date:
            current = stats[cid]["last_sync"]
            if current is None or gsc_date > current:
                stats[cid]["last_sync"] = gsc_date

    result = []
    for client in all_clients:
        cid = client["id"]
        s   = stats.get(cid, {"total": 0, "crescita": 0, "calo": 0, "last_sync": None})
        result.append({
            "id":                client["id"],
            "name":              client["name"],
            "sector":            client.get("sector") or "",
            "total_keywords":    s["total"],
            "keywords_crescita": s["crescita"],
            "keywords_calo":     s["calo"],
            "last_sync":         s["last_sync"],
        })

    result.sort(key=lambda x: x["keywords_calo"], reverse=True)
    return result
