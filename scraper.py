#!/usr/bin/env python3
"""Config-driven web scraper with page-level checkpointing and CSV/Excel export."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag

HERE = os.path.dirname(os.path.abspath(__file__))
UA = "ResumableWebScraper/2.1 (+https://github.com/shoaibmahmud244-lang/resumable-web-scraper)"
RETRY_STATUS = {429, 500, 502, 503, 504}


def load_config(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
        return data


def fetch(
    session: requests.Session, url: str, timeout: int, retries: int, backoff: float
) -> str:
    """GET with exponential backoff. Returns response text or raises RuntimeError."""
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = session.get(url, timeout=timeout)
            if resp.status_code in RETRY_STATUS:
                raise requests.HTTPError(f"status {resp.status_code}")
            resp.raise_for_status()
            declared = resp.encoding
            if not declared or declared.lower() in ("iso-8859-1", "latin-1", "latin"):
                resp.encoding = resp.apparent_encoding or "utf-8"
            return resp.text
        except Exception as exc:
            last = exc
            if attempt == retries:
                break
            wait = backoff * (2**attempt)
            print(f"    retry {attempt + 1}/{retries} in {wait:.1f}s ({exc})")
            time.sleep(wait)
    raise RuntimeError(f"failed {url}: {last}")


def attr_text(node: Tag, name: str | None) -> str:
    """Return a BeautifulSoup tag attribute normalised as a string."""
    if not name:
        return ""
    value = node.get(name, "")
    if isinstance(value, (list, tuple)):
        return " ".join(str(v) for v in value)
    return str(value) if value else ""


def extract(item: Tag, spec: dict[str, Any], base_url: str) -> Any:
    """Extract one field from an HTML element using selector + attr / regex / map."""
    if spec.get("all"):
        found: list[str] = []
        for node in item.select(spec["selector"]):
            value = (
                attr_text(node, spec.get("attr"))
                if "attr" in spec
                else node.get_text(" ", strip=True)
            )
            if "regex" in spec:
                match = re.search(spec["regex"], value)
                value = match.group(1).strip() if match else ""
            if spec.get("absolute") and value:
                value = urljoin(base_url, value)
            if value:
                found.append(value)
        return ", ".join(found)

    node = item.select_one(spec["selector"])
    if node is None:
        return ""
    if "map" in spec:
        key = attr_text(node, spec.get("attr", "class"))
        for token, mapped_val in spec["map"].items():
            if token in key:
                return mapped_val
        return ""
    if "attr" in spec:
        value = attr_text(node, spec["attr"])
    else:
        value = node.get_text(" ", strip=True)
    if "regex" in spec:
        match = re.search(spec["regex"], value)
        value = match.group(1).strip() if match else ""
    if spec.get("absolute") and value:
        value = urljoin(base_url, value)
    return value


def rows_from(html: str, site: dict[str, Any], current_url: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict[str, Any]] = []
    for item in soup.select(site["item_selector"]):
        row = {
            name: extract(item, spec, current_url)
            for name, spec in site["fields"].items()
        }
        if any(row.values()):
            out.append(row)
    return out


def load_state(path: str) -> dict[str, Any]:
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                state: dict[str, Any] = json.load(fh)
                return state
        except (OSError, ValueError):
            pass
    return {"pages_done": [], "rows": 0}


def save_state(path: str, state: dict[str, Any]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    os.replace(tmp, path)


def seen_ids(csv_path: str, id_field: str) -> set[str]:
    seen: set[str] = set()
    if not os.path.exists(csv_path):
        return seen
    with open(csv_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            value = row.get(id_field, "")
            if value:
                seen.add(value)
    return seen


def page_url(site: dict[str, Any], page: int) -> str:
    if "page_url" in site:
        return str(site["page_url"]).format(page=page)
    return str(site["url"])


def scrape_site(
    site: dict[str, Any], args: argparse.Namespace, session: requests.Session
) -> tuple[str, int]:
    name = site["name"]
    out_csv = os.path.join(args.out, f"{name}.csv")
    state_path = os.path.join(args.out, f"{name}.state.json")
    fields = list(site["fields"].keys())
    id_field = site.get("id_field", fields[0])

    if args.fresh:
        for path in (out_csv, state_path):
            if os.path.exists(path):
                os.remove(path)

    state = load_state(state_path)
    done: set[int] = set(state["pages_done"])
    seen = seen_ids(out_csv, id_field)
    new_file = not os.path.exists(out_csv) or os.path.getsize(out_csv) == 0
    max_pages = int(site.get("max_pages", 1))
    delay = float(site.get("delay", 0.5))
    added = skipped = accounted = 0

    status_msg = (
        f"[{name}] {max_pages} page(s) total, {len(done)} already done, resuming"
        if done
        else f"[{name}] {max_pages} page(s) to scrape"
    )
    print(status_msg)

    with open(out_csv, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        if new_file:
            writer.writeheader()
        for page in range(1, max_pages + 1):
            if page in done:
                continue
            url = page_url(site, page)
            html = fetch(session, url, args.timeout, args.retries, args.backoff)
            for row in rows_from(html, site, url):
                key = str(row.get(id_field, ""))
                if key and key in seen:
                    skipped += 1
                    continue
                writer.writerow(row)
                if key:
                    seen.add(key)
                added += 1
            fh.flush()
            done.add(page)
            state["pages_done"] = sorted(done)
            state["rows"] = int(state.get("rows", 0)) + (added - accounted)
            accounted = added
            save_state(state_path, state)
            print(f"    page {page}/{max_pages}  rows so far: {len(seen)}")
            if page < max_pages:
                time.sleep(delay)

    print(
        f"[{name}] done: +{added} new, {skipped} duplicate(s) skipped, "
        f"{len(seen)} unique -> {out_csv}"
    )
    return out_csv, len(seen)


def to_excel(csv_path: str) -> str | None:
    """Convert CSV to styled .xlsx when openpyxl is installed."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
    except ImportError:
        print("    openpyxl not installed - skipping .xlsx (CSV still written)")
        return None

    with open(csv_path, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    if not rows:
        return None

    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = os.path.splitext(os.path.basename(csv_path))[0][:31]
    for row in rows:
        ws.append(row)

    fill = PatternFill("solid", fgColor="0F2A43")
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = fill
        cell.alignment = Alignment(vertical="center")
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for index, column in enumerate(ws.columns, start=1):
        width = max(
            (len(str(c.value)) for c in column if c.value is not None), default=10
        )
        col_letter = ws.cell(row=1, column=index).column_letter
        ws.column_dimensions[col_letter].width = min(max(width + 2, 12), 60)

    xlsx_path = os.path.splitext(csv_path)[0] + ".xlsx"
    wb.save(xlsx_path)
    print(f"    excel written: {xlsx_path} ({len(rows) - 1} data rows)")
    return xlsx_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Config-driven, resumable web scraper.")
    parser.add_argument("--config", default=os.path.join(HERE, "sites.json"))
    parser.add_argument("--site", help="scrape only this site name")
    parser.add_argument("--all", action="store_true", help="scrape every site in config")
    parser.add_argument("--out", default=os.path.join(HERE, "out"))
    parser.add_argument("--fresh", action="store_true", help="discard checkpoint")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--backoff", type=float, default=1.5)
    args = parser.parse_args()

    sites = load_config(args.config)["sites"]
    targets = (
        [s for s in sites if args.site in (None, s["name"])]
        if (args.site or args.all)
        else sites
    )
    if not targets:
        available = ", ".join(s["name"] for s in sites)
        sys.exit(f"no site matched {args.site!r} (have: {available})")

    os.makedirs(args.out, exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept-Language": "en-GB,en;q=0.9"})

    for site in targets:
        csv_path, count = scrape_site(site, args, session)
        to_excel(csv_path)
        print(f"    {count} unique rows\n")


if __name__ == "__main__":
    main()
