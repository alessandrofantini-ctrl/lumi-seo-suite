import os
import requests
from requests.adapters import HTTPAdapter, Retry

def build_session():
    s = requests.Session()
    retries = Retry(total=3, backoff_factor=0.6,
                    status_forcelist=(429, 500, 502, 503, 504),
                    allowed_methods=("GET",), raise_on_status=False)
    s.mount("https://", HTTPAdapter(max_retries=retries))
    return s

HTTP = build_session()

def get_serp_data(query: str, gl: str, hl: str, domain: str) -> dict | None:
    api_key = os.getenv("SERPAPI_KEY")
    if not api_key:
        raise RuntimeError("SERPAPI_KEY non configurata sul server")
    params = {
        "engine":        "google",
        "q":             query,
        "api_key":       api_key,
        "hl":            hl,
        "gl":            gl,
        "google_domain": domain,
    }
    try:
        r = HTTP.get("https://serpapi.com/search", params=params, timeout=25)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None
