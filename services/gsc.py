import json
import os
from datetime import date, timedelta

from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]


def _get_service():
    sa_json = os.getenv("GSC_SERVICE_ACCOUNT_JSON")
    if not sa_json:
        raise RuntimeError(
            "Variabile d'ambiente GSC_SERVICE_ACCOUNT_JSON mancante. "
            "Incolla il contenuto del file JSON del service account su Render."
        )
    info = json.loads(sa_json)
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("searchconsole", "v1", credentials=creds, cache_discovery=False)


def fetch_gsc_queries(site_url: str, days: int = 28) -> list[dict]:
    """
    Scarica le query dalla Search Console per il sito indicato.
    Restituisce una lista di dict: {query, clicks, impressions, ctr, position}
    """
    # GSC ha un ritardo di ~3 giorni
    end = date.today() - timedelta(days=3)
    start = end - timedelta(days=days)

    service = _get_service()
    body = {
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "dimensions": ["query"],
        "rowLimit": 1000,
    }
    resp = service.searchanalytics().query(siteUrl=site_url, body=body).execute()
    rows = resp.get("rows", [])

    return [
        {
            "query": r["keys"][0],
            "clicks": int(r.get("clicks", 0)),
            "impressions": int(r.get("impressions", 0)),
            "ctr": round(r.get("ctr", 0.0), 4),
            "position": round(r.get("position", 0.0), 1),
        }
        for r in rows
    ]


def fetch_gsc_page_metrics(property_url: str, page_url: str) -> dict | None:
    """
    Recupera metriche GSC aggregate per un URL specifico
    (ultimi 28 giorni, tutte le query che portano a quella pagina).
    Ritorna { position, clicks, impressions, ctr } o None se la pagina
    non appare in GSC.
    """
    # GSC ha un ritardo di ~3 giorni
    end = date.today() - timedelta(days=3)
    start = end - timedelta(days=28)

    service = _get_service()
    body = {
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "dimensions": ["page"],
        "dimensionFilterGroups": [{
            "filters": [{
                "dimension": "page",
                "operator": "equals",
                "expression": page_url,
            }]
        }],
    }
    resp = service.searchanalytics().query(siteUrl=property_url, body=body).execute()
    rows = resp.get("rows", [])

    if not rows:
        return None

    r = rows[0]
    return {
        "clicks":      int(r.get("clicks", 0)),
        "impressions": int(r.get("impressions", 0)),
        "ctr":         round(r.get("ctr", 0.0), 4),
        "position":    round(r.get("position", 0.0), 1),
    }


def fetch_gsc_site_metrics(site_url: str, days: int = 28) -> dict:
    """
    Metriche aggregate del sito intero (ultimi N giorni).
    Ritorna { clicks, impressions, ctr, avg_position }
    """
    end = date.today() - timedelta(days=3)
    start = end - timedelta(days=days)
    service = _get_service()
    body = {
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "dimensions": [],  # nessuna dimensione = totale sito
        "rowLimit": 1,
    }
    resp = service.searchanalytics().query(siteUrl=site_url, body=body).execute()
    rows = resp.get("rows", [])
    if not rows:
        return {"clicks": 0, "impressions": 0, "ctr": 0.0, "avg_position": None}
    r = rows[0]
    return {
        "clicks":       int(r.get("clicks", 0)),
        "impressions":  int(r.get("impressions", 0)),
        "ctr":          round(r.get("ctr", 0.0), 4),
        "avg_position": round(r.get("position", 0.0), 1),
    }
