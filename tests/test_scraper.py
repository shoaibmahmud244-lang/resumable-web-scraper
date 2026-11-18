from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests

from scraper import fetch, load_state, rows_from, save_state, seen_ids

SAMPLE_HTML = """
<html><body>
  <article class="product_pod">
    <h3><a href="catalogue/book-1_1/index.html" title="A Light in the Attic">A Light...</a></h3>
    <p class="price_color">£51.77</p>
    <p class="instock availability"> In stock </p>
    <p class="star-rating Three"></p>
  </article>
  <article class="product_pod">
    <h3><a href="catalogue/book-2_2/index.html" title="Tipping the Velvet">Tipping...</a></h3>
    <p class="price_color">€12,50</p>
    <p class="instock availability">Out of stock</p>
    <p class="star-rating Five"></p>
  </article>
</body></html>
"""

SITE_CFG = {
    "name": "books",
    "item_selector": "article.product_pod",
    "fields": {
        "title": {"selector": "h3 a", "attr": "title"},
        "price": {"selector": "p.price_color", "regex": "([£$€]?[0-9]+[.,][0-9]{2})"},
        "availability": {"selector": "p.instock.availability"},
        "rating": {
            "selector": "p.star-rating",
            "attr": "class",
            "map": {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5},
        },
        "url": {"selector": "h3 a", "attr": "href", "absolute": True},
    },
}


def test_rows_from_extracts_fields_and_multi_valued_class() -> None:
    rows = rows_from(SAMPLE_HTML, SITE_CFG, "https://books.toscrape.com/index.html")
    assert len(rows) == 2
    assert rows[0] == {
        "title": "A Light in the Attic",
        "price": "£51.77",
        "availability": "In stock",
        "rating": 3,
        "url": "https://books.toscrape.com/catalogue/book-1_1/index.html",
    }
    assert rows[1]["price"] == "€12,50"
    assert rows[1]["rating"] == 5


def test_state_roundtrip_and_atomic_write(tmp_path: Path) -> None:
    state_file = tmp_path / "books.state.json"
    assert load_state(str(state_file)) == {"pages_done": [], "rows": 0}

    save_state(str(state_file), {"pages_done": [1, 2], "rows": 40})
    assert load_state(str(state_file)) == {"pages_done": [1, 2], "rows": 40}
    assert not (tmp_path / "books.state.json.tmp").exists()


def test_seen_ids_reads_existing_csv(tmp_path: Path) -> None:
    csv_file = tmp_path / "books.csv"
    csv_file.write_text("title,url\nBook 1,https://example.com/1\n", encoding="utf-8")
    assert seen_ids(str(csv_file), "url") == {"https://example.com/1"}


def test_fetch_retries_on_503_then_succeeds() -> None:
    session = MagicMock(spec=requests.Session)
    bad_resp = MagicMock(status_code=503)
    ok_resp = MagicMock(status_code=200, encoding="utf-8", text="<html>ok</html>")
    session.get.side_effect = [bad_resp, ok_resp]

    result = fetch(session, "https://example.com", timeout=5, retries=1, backoff=0.01)
    assert result == "<html>ok</html>"
    assert session.get.call_count == 2


def test_fetch_raises_after_exhausting_retries() -> None:
    session = MagicMock(spec=requests.Session)
    session.get.side_effect = requests.ConnectionError("network down")
    with pytest.raises(RuntimeError, match="failed https://example.com"):
        fetch(session, "https://example.com", timeout=5, retries=1, backoff=0.01)
