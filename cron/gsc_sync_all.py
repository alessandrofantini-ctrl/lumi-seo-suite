"""
Script eseguito settimanalmente da Render Cron Job.
Sincronizza GSC per tutti i clienti con gsc_property configurata.
"""
import asyncio
import os
import sys
from datetime import datetime

from supabase import create_client

# Importa la logica di sync già esistente dal service
from services.gsc import fetch_gsc_queries


async def sync_all_clients() -> None:
    supabase_url = os.environ.get("SUPABASE_URL")
    service_role_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

    if not supabase_url or not service_role_key:
        print("ERRORE: SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY non configurate.")
        sys.exit(1)

    supabase = create_client(supabase_url, service_role_key)

    # Carica tutti i clienti con gsc_property configurata
    clients = (
        supabase.table("clients")
        .select("id, name, gsc_property")
        .not_.is_("gsc_property", "null")
        .neq("gsc_property", "")
        .execute()
    )

    print(f"[{datetime.utcnow().isoformat()}] Sync GSC automatico — {len(clients.data)} clienti")

    for client in clients.data:
        try:
            # Carica keyword del cliente (keyword + position attuale per position_prev)
            keywords = (
                supabase.table("keyword_history")
                .select("id, keyword, position")
                .eq("client_id", client["id"])
                .execute()
            )

            if not keywords.data:
                print(f"  [{client['name']}] Nessuna keyword — skip")
                continue

            # Fetch dati GSC (riusa service esistente — restituisce lista di dict)
            rows = fetch_gsc_queries(client["gsc_property"])

            # Costruisce dizionario {query_lower: metrics} per lookup O(1)
            gsc_data: dict[str, dict] = {
                r["query"].lower(): {
                    "position":    r["position"],
                    "clicks":      r["clicks"],
                    "impressions": r["impressions"],
                    "ctr":         r["ctr"],
                }
                for r in rows
            }

            now = datetime.utcnow().isoformat()
            synced = 0

            for kw in keywords.data:
                metrics = gsc_data.get(kw["keyword"].lower())
                if not metrics:
                    continue

                update_payload: dict = {
                    "position":            metrics["position"],
                    "clicks":              metrics["clicks"],
                    "impressions":         metrics["impressions"],
                    "ctr":                 metrics["ctr"],
                    "gsc_updated_at":      now,
                    "position_updated_at": now,
                }
                # Salva position_prev solo se ne esisteva già una
                if kw["position"] is not None:
                    update_payload["position_prev"] = kw["position"]

                supabase.table("keyword_history").update(update_payload).eq("id", kw["id"]).execute()

                # Inserisci snapshot storico
                supabase.table("keyword_position_history").insert({
                    "keyword_id":  kw["id"],
                    "client_id":   client["id"],
                    "position":    metrics["position"],
                    "clicks":      metrics["clicks"],
                    "impressions": metrics["impressions"],
                    "ctr":         metrics["ctr"],
                    "recorded_at": now,
                }).execute()

                synced += 1

            print(f"  [{client['name']}] {synced}/{len(keywords.data)} keyword aggiornate")

        except Exception as e:
            # Non bloccare gli altri clienti se uno fallisce
            print(f"  [{client['name']}] ERRORE: {e}")
            continue

    print(f"[{datetime.utcnow().isoformat()}] Sync completato")


if __name__ == "__main__":
    asyncio.run(sync_all_clients())
