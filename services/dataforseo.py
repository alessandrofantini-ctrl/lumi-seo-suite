import logging
import httpx

logger = logging.getLogger(__name__)

_ENDPOINT = "https://api.dataforseo.com/v3/keywords_data/google_ads/search_volume/live"
_CHUNK_SIZE = 1000


async def get_search_volume(
    keywords: list[str],
    language_code: str,
    location_code: int,
    login: str,
    password: str,
) -> dict[str, int | None]:
    """
    Chiama DataForSEO keywords_data/google_ads/search_volume/live.
    Ritorna dict {keyword: volume_mensile} per ogni keyword passata.
    Keyword non trovate → None.
    Max 1000 keyword per chiamata — se lista > 1000 chunka automaticamente.
    """
    result: dict[str, int | None] = {kw: None for kw in keywords}

    async with httpx.AsyncClient(auth=(login, password), timeout=30.0) as client:
        for i in range(0, len(keywords), _CHUNK_SIZE):
            chunk = keywords[i : i + _CHUNK_SIZE]
            try:
                resp = await client.post(
                    _ENDPOINT,
                    json=[{
                        "keywords": chunk,
                        "language_code": language_code,
                        "location_code": location_code,
                    }],
                )
                resp.raise_for_status()
                data = resp.json()
                items = ((data.get("tasks") or [{}])[0]).get("result") or []
                for item in items:
                    kw = item.get("keyword")
                    vol = item.get("search_volume")
                    if kw is not None:
                        result[kw] = vol
            except Exception as exc:
                logger.warning("DataForSEO error for chunk starting at %d: %s", i, exc)

    return result
