"""
Tests per services/scraper.py — funzioni pure (zero chiamate di rete).

Copre:
- tokenize(): estrazione token con esclusione stopwords e token corti
- build_serp_snapshot(): formattazione risultati SerpAPI
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from services.scraper import tokenize, build_serp_snapshot, STOPWORDS


# ── tokenize() ────────────────────────────────────────────────────────────────

def test_tokenize_basic():
    result = tokenize("seo agency marketing")
    assert "seo" in result
    assert "agency" in result
    assert "marketing" in result


def test_tokenize_esclude_stopwords():
    # "the", "and", "of", "for" sono in STOPWORDS
    result = tokenize("the best seo and marketing for all")
    assert "the" not in result
    assert "and" not in result
    assert "for" not in result
    assert "seo" in result
    assert "marketing" in result


def test_tokenize_lunghezza_minima():
    # Il regex richiede almeno 3 caratteri
    result = tokenize("a go seo")
    assert "a" not in result
    assert "go" not in result
    assert "seo" in result


def test_tokenize_case_insensitive():
    result = tokenize("SEO Marketing AGENZIA")
    assert "seo" in result
    assert "marketing" in result
    assert "agenzia" in result


def test_tokenize_stringa_vuota():
    assert tokenize("") == []


def test_tokenize_none():
    assert tokenize(None) == []


def test_tokenize_caratteri_speciali():
    # Punteggiatura deve essere ignorata
    result = tokenize("seo! marketing? agenzia.")
    assert "seo" in result
    assert "marketing" in result
    assert "agenzia" in result


def test_tokenize_accenti():
    # Caratteri accentati italiani supportati dal regex
    result = tokenize("perché società così")
    assert len(result) > 0


# ── build_serp_snapshot() ─────────────────────────────────────────────────────

def test_serp_snapshot_base():
    serp_json = {
        "organic_results": [
            {"position": 1, "title": "Best SEO", "link": "https://a.com", "snippet": "We do SEO", "source": "a.com"},
            {"position": 2, "title": "SEO Tips",  "link": "https://b.com", "snippet": "Learn SEO", "source": "b.com"},
        ],
        "related_questions": [
            {"question": "Cos'è la SEO?"},
            {"question": "Come funziona la SEO?"},
        ],
        "related_searches": [
            {"query": "agenzia seo milano"},
        ],
    }
    snapshot = build_serp_snapshot(serp_json, max_items=6)
    assert len(snapshot["organic"]) == 2
    assert snapshot["organic"][0]["title"] == "Best SEO"
    assert snapshot["organic"][0]["position"] == 1
    assert "Cos'è la SEO?" in snapshot["paa"]
    assert "agenzia seo milano" in snapshot["related_searches"]


def test_serp_snapshot_rispetta_max_items():
    serp_json = {
        "organic_results": [
            {"position": i, "title": f"Result {i}", "link": f"https://ex{i}.com", "snippet": "", "source": ""}
            for i in range(1, 10)
        ]
    }
    snapshot = build_serp_snapshot(serp_json, max_items=3)
    assert len(snapshot["organic"]) == 3


def test_serp_snapshot_rileva_features():
    serp_json = {
        "answer_box":      {"type": "organic", "title": "Risposta diretta"},
        "knowledge_graph": {"title": "Entità"},
    }
    snapshot = build_serp_snapshot(serp_json, max_items=6)
    assert "answer_box"      in snapshot["features"]
    assert "knowledge_graph" in snapshot["features"]


def test_serp_snapshot_input_vuoto():
    snapshot = build_serp_snapshot({}, max_items=6)
    assert snapshot["organic"]          == []
    assert snapshot["paa"]             == []
    assert snapshot["related_searches"] == []
    assert snapshot["features"]         == []


def test_serp_snapshot_struttura_organic():
    serp_json = {
        "organic_results": [
            {"position": 1, "title": "Title", "link": "https://x.com", "snippet": "Snip", "source": "x.com"},
        ]
    }
    snapshot = build_serp_snapshot(serp_json, max_items=6)
    item = snapshot["organic"][0]
    assert set(item.keys()) >= {"position", "title", "link", "snippet", "source"}
