import re
import json
from collections import Counter
from urllib.parse import urlparse, urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter, Retry

# ══════════════════════════════════════════════
#  HTTP SESSION
# ══════════════════════════════════════════════

def build_session():
    s = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=0.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        raise_on_status=False,
    )
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.mount("http://",  HTTPAdapter(max_retries=retries))
    return s

HTTP = build_session()

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

STOPWORDS = set("""
a al allo alla alle agli all and are as at be by con che da dal dalla dalle degli dei del della delle di
e ed en est et for from il in is it la le lo los las les more nel nei of on or per su the to un una uno
und une with y zu""".split())

# ══════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════

def safe_text(s: str) -> str:
    if not s:
        return ""
    return re.sub(r"\s+", " ", s).strip()

def domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""

def tokenize(text: str):
    text = (text or "").lower()
    tokens = re.findall(r"[a-zàèéìòùäöüßñç0-9]{3,}", text, flags=re.I)
    return [t for t in tokens if t not in STOPWORDS]

def normalize_sentence_case(text: str) -> str:
    t = safe_text(text)
    if not t:
        return ""
    if t.isupper() and len(t) > 6:
        t = t.lower()
    return t[0].upper() + t[1:] if len(t) > 1 else t.upper()

# ══════════════════════════════════════════════
#  SOUP HELPERS
# ══════════════════════════════════════════════

def remove_boilerplate(soup: BeautifulSoup):
    for tag in soup(["script", "style", "noscript", "svg", "canvas", "iframe"]):
        tag.decompose()
    for selector in ["nav", "header", "footer", "aside", "form"]:
        for tag in soup.select(selector):
            tag.decompose()
    for cls in ["cookie", "cookies", "newsletter", "modal", "popup"]:
        for tag in soup.select(f".{cls}"):
            tag.decompose()
    return soup

def detect_main_container(soup: BeautifulSoup):
    for tag in ["article", "main"]:
        el = soup.find(tag)
        if el and len(el.get_text(" ", strip=True)) > 600:
            return el
    return soup.body if soup.body else soup

def extract_json_ld(soup: BeautifulSoup):
    out = []
    for sc in soup.find_all("script", type="application/ld+json")[:12]:
        txt = sc.get_text(strip=True)
        if txt:
            try:
                out.append(json.loads(txt))
            except Exception:
                pass
    return out

# ══════════════════════════════════════════════
#  SCRAPING PAGINA SINGOLA
# ══════════════════════════════════════════════

def scrape_site_content(url: str, include_meta=True, include_schema=True, max_text_chars=9000) -> dict | None:
    data = {
        "url": url, "domain": domain_of(url),
        "title": "", "meta_description": "", "canonical": "",
        "h1": "", "h2": [], "h3": [],
        "word_count": 0, "text_sample": "", "top_terms": [],
        "lang": "", "schema_types": [], "has_faq_schema": False,
        "question_headings": [],
    }
    try:
        resp = HTTP.get(url, headers=UA, timeout=18, allow_redirects=True)
        if resp.status_code >= 400 or not resp.text:
            return None

        soup = BeautifulSoup(resp.text, "html.parser")
        soup = remove_boilerplate(soup)

        html_tag = soup.find("html")
        if html_tag and html_tag.get("lang"):
            data["lang"] = safe_text(html_tag.get("lang"))

        if include_meta:
            if soup.title and soup.title.string:
                data["title"] = safe_text(soup.title.string)
            md = soup.find("meta", attrs={"name": re.compile(r"description", re.I)})
            if md and md.get("content"):
                data["meta_description"] = safe_text(md.get("content"))
            canon = soup.find("link", rel=lambda x: x and "canonical" in x.lower())
            if canon and canon.get("href"):
                data["canonical"] = safe_text(canon.get("href"))

        main = detect_main_container(soup)

        for tag in main.find_all(["h1", "h2", "h3"])[:80]:
            txt = safe_text(tag.get_text(" ", strip=True))
            if not txt:
                continue
            if tag.name == "h1" and not data["h1"]:
                data["h1"] = txt
            elif tag.name == "h2" and len(data["h2"]) < 30:
                data["h2"].append(txt)
            elif tag.name == "h3" and len(data["h3"]) < 45:
                data["h3"].append(txt)
            if "?" in txt:
                data["question_headings"].append(txt)

        p_text  = " ".join(safe_text(p.get_text(" ", strip=True)) for p in main.find_all("p"))
        li_text = " ".join(safe_text(li.get_text(" ", strip=True)) for li in main.find_all("li")[:140])
        text_content = safe_text((p_text + " " + li_text).strip())[:max_text_chars]

        data["word_count"]  = len(text_content.split()) if text_content else 0
        data["text_sample"] = text_content[:2400]
        data["top_terms"]   = [t for t, _ in Counter(tokenize(text_content)).most_common(25)]

        if include_schema:
            types, has_faq = set(), False
            for item in extract_json_ld(soup):
                for it in (item if isinstance(item, list) else [item]):
                    if isinstance(it, dict) and "@type" in it:
                        t = it["@type"]
                        for tt in (t if isinstance(t, list) else [t]):
                            types.add(str(tt))
                            if "FAQPage" in str(tt):
                                has_faq = True
            data["schema_types"]    = sorted(types)[:25]
            data["has_faq_schema"]  = has_faq

        return data
    except Exception:
        return None

# ══════════════════════════════════════════════
#  SERP SNAPSHOT
# ══════════════════════════════════════════════

def build_serp_snapshot(serp_json: dict, max_items: int) -> dict:
    snapshot = {"organic": [], "paa": [], "related_searches": [], "features": []}
    if not serp_json:
        return snapshot
    for res in serp_json.get("organic_results", [])[:max_items]:
        snapshot["organic"].append({
            "position": res.get("position"),
            "title":    res.get("title"),
            "link":     res.get("link"),
            "snippet":  res.get("snippet"),
            "source":   res.get("source"),
        })
    for q in serp_json.get("related_questions", [])[:20]:
        if q.get("question"):
            snapshot["paa"].append(q["question"])
    for r in serp_json.get("related_searches", [])[:20]:
        if r.get("query"):
            snapshot["related_searches"].append(r["query"])
    for feature in ["answer_box", "knowledge_graph", "shopping_results", "local_results", "top_stories"]:
        if serp_json.get(feature):
            snapshot["features"].append(feature)
    return snapshot

# ══════════════════════════════════════════════
#  BATCH BRIEF — Rexel facets + competitor
# ══════════════════════════════════════════════

_COUNT_RE    = re.compile(r"\s*\(\d+\)\s*")
_FILTER_RE   = re.compile(r"^(.+?)\s*\(\d+\)\s*$")
_FILTER_STOP = {
    "filtri", "mostra", "ordina per", "rilevanza", "prezzo listino",
    "prezzo netto", "in stock", "a magazzino", "disponibile",
    "tutti", "tutte", "altri", "altro", "vedi",
}


def scrape_rexel_facets(url: str) -> dict:
    """
    Estrae brands e filtri (facets) da una pagina categoria Rexel.
    Ritorna { brands: list[str], filters: list[str] }.
    Se l'URL non contiene rexel.it o lo scraping fallisce: entrambe le liste vuote.
    """
    if "rexel.it" not in url:
        return {"brands": [], "filters": []}
    try:
        r = HTTP.get(url, headers=UA, timeout=18, allow_redirects=True)
        if r.status_code >= 400:
            return {"brands": [], "filters": []}
        soup = BeautifulSoup(r.text, "html.parser")

        # ── Brands ────────────────────────────────────────────────────────
        brands: list[str] = []
        brands_container  = soup.find(id="collapseBrands")
        if brands_container:
            seen: set[str] = set()
            for el in brands_container.find_all(["a", "label", "span"]):
                raw   = safe_text(el.get_text(" ", strip=True))
                clean = _COUNT_RE.sub("", raw).strip()
                key   = clean.lower()
                if clean and len(clean) >= 2 and key not in seen:
                    seen.add(key)
                    brands.append(clean)

        # ── Filtri ────────────────────────────────────────────────────────
        filters: list[str] = []
        page_text = soup.get_text("\n", strip=True)
        lines     = [l.strip() for l in page_text.splitlines() if l.strip()]
        in_window = False
        for line in lines:
            ll = line.lower()
            if not in_window:
                if ll == "filtri" or ll.startswith("filtri "):
                    in_window = True
            else:
                if "trovati" in ll:
                    break
                m = _FILTER_RE.match(line)
                if m:
                    name = m.group(1).strip()
                    if (
                        len(name) >= 6
                        and name.lower() not in _FILTER_STOP
                        and not any(sw in name.lower() for sw in _FILTER_STOP)
                    ):
                        filters.append(name)

        return {"brands": brands[:30], "filters": filters[:30]}
    except Exception:
        return {"brands": [], "filters": []}


def scrape_competitor_for_brief(url: str, timeout: int = 18) -> dict | None:
    """
    Scraping leggero per competitor analysis nel batch brief.
    Ritorna { url, h1, h2[], h3[], bullets[], word_count, text_sample } o None.
    """
    try:
        r = HTTP.get(url, headers=UA, timeout=timeout, allow_redirects=True)
        if r.status_code >= 400:
            return None
        soup = BeautifulSoup(r.text, "html.parser")

        for tag in soup(["script", "style", "noscript", "svg", "iframe"]):
            tag.decompose()
        for selector in ["nav", "header", "footer", "aside", "form"]:
            for el in soup.select(selector):
                el.decompose()
        for cls_pattern in ["cookie", "newsletter", "modal", "popup"]:
            for el in soup.select(f"[class*={cls_pattern}]"):
                el.decompose()

        main = detect_main_container(soup)

        h1_tag = main.find("h1")
        h1     = safe_text(h1_tag.get_text(" ", strip=True)) if h1_tag else ""
        h2s    = [safe_text(h.get_text(" ", strip=True)) for h in main.find_all("h2")][:30]
        h3s    = [safe_text(h.get_text(" ", strip=True)) for h in main.find_all("h3")][:45]

        bullets: list[str] = []
        for li in main.find_all("li"):
            text = safe_text(li.get_text(" ", strip=True))
            if 18 <= len(text) <= 200:
                bullets.append(text)
            if len(bullets) >= 45:
                break

        p_text     = " ".join(safe_text(p.get_text(" ", strip=True)) for p in main.find_all("p"))
        li_text    = " ".join(safe_text(li.get_text(" ", strip=True)) for li in main.find_all("li")[:140])
        combined   = (p_text + " " + li_text).strip()
        word_count = len(combined.split()) if combined else 0

        return {
            "url":         url,
            "h1":          h1,
            "h2":          [h for h in h2s if h],
            "h3":          [h for h in h3s if h],
            "bullets":     [b for b in bullets if b],
            "word_count":  word_count,
            "text_sample": combined[:3200],
        }
    except Exception:
        return None


# ══════════════════════════════════════════════
#  AGGREGAZIONE INSIGHT COMPETITOR
# ══════════════════════════════════════════════

def aggregate_competitor_insights(competitors: list, target_lang: str) -> dict:
    h2_all, terms_all, q_all = [], [], []
    for c in competitors:
        h2_all  += [safe_text(h).lower() for h in c.get("h2", [])[:30] if safe_text(h)]
        terms_all += [t.lower() for t in c.get("top_terms", [])[:25]]
        q_all   += [safe_text(q).lower() for q in c.get("question_headings", [])[:25] if safe_text(q)]

    def norm(h):
        h = re.sub(r"\s+", " ", h)
        return re.sub(r"[^\w\sàèéìòùäöüßñç-]", "", h).strip()

    top_h2    = [normalize_sentence_case(x) for x, _ in Counter([norm(x) for x in h2_all if x]).most_common(12)]
    top_terms = [x for x, _ in Counter([x for x in terms_all if x and x not in STOPWORDS]).most_common(20)]
    top_q     = [normalize_sentence_case(x) for x, _ in Counter([norm(x) for x in q_all if x]).most_common(8)]

    avg_word_count = round(
        sum(c.get("word_count", 0) for c in competitors) / max(len(competitors), 1)
    ) if competitors else 0

    return {"top_h2": top_h2, "top_terms": top_terms, "top_questions": top_q, "avg_word_count": avg_word_count}

# ══════════════════════════════════════════════
#  SCRAPING PROFONDO CLIENTE
# ══════════════════════════════════════════════

def scrape_page_light(url: str) -> dict | None:
    try:
        resp = HTTP.get(url, headers=UA, timeout=12, allow_redirects=True)
        if resp.status_code >= 400:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        soup = remove_boilerplate(soup)
        main = detect_main_container(soup)
        return {
            "url":   url,
            "title": safe_text(soup.title.string) if soup.title else "",
            "h1":    safe_text(main.find("h1").get_text()) if main.find("h1") else "",
            "h2s":   [safe_text(h.get_text()) for h in main.find_all("h2")[:10]],
            "text":  safe_text(main.get_text(" ", strip=True))[:3000],
        }
    except Exception:
        return None

def scrape_client_deep(base_url: str, keyword: str, max_pages: int = 6) -> list:
    visited = set()
    pages_data = []

    home = scrape_page_light(base_url)
    if not home:
        return []
    visited.add(base_url)
    pages_data.append(("homepage", home))

    try:
        resp = HTTP.get(base_url, headers=UA, timeout=12)
        soup = BeautifulSoup(resp.text, "html.parser")
        priority_keywords = ["prodott", "serviz", "soluzion", "chi-siamo", "about", "categor", "product", "service"]
        base_domain = urlparse(base_url).netloc
        priority_links = []
        for a in soup.find_all("a", href=True):
            href = urljoin(base_url, a["href"])
            if urlparse(href).netloc == base_domain and any(pk in href.lower() for pk in priority_keywords):
                priority_links.append(href)
        priority_links = list(dict.fromkeys(priority_links))
    except Exception:
        priority_links = []

    def score_page(pd):
        if not pd:
            return 0
        kw_tokens   = tokenize(keyword)
        page_tokens = tokenize(pd.get("text", ""))
        return sum(1 for t in kw_tokens if t in page_tokens)

    futures_map = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        for url in priority_links[:15]:
            if url not in visited and len(visited) < max_pages + 1:
                visited.add(url)
                futures_map[ex.submit(scrape_page_light, url)] = url

        scored = []
        for fut in as_completed(futures_map):
            pd = fut.result()
            if pd and len(pd.get("text", "")) > 150:
                scored.append((score_page(pd), futures_map[fut], pd))

    scored.sort(key=lambda x: x[0], reverse=True)
    for _, url, pd in scored[:max_pages]:
        label = next(
            (pk for pk in ["prodott", "serviz", "soluzion", "about", "chi-siamo"] if pk in url.lower()),
            "pagina"
        )
        pages_data.append((label, pd))

    return pages_data
