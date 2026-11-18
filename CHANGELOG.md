# Changelog

## [2.1.0] - 2026-11-18
### Added
- Added `pyproject.toml`, `pytest` unit test suite, `ruff`/`mypy` checks, and GitHub Actions CI.
- Added full type annotations to `scraper.py`.

### Removed
- Removed dead duplicate `spec.get("all")` branch inside `extract()`.
- Removed generated `out/` artifacts and `make_card.py` / `scraper-proof.png` from version control.

## [2.0.0] - 2026-09-29
### Fixed
- **Currency encoding & regex (`fetch`, `sites.json`)**: Fallback to `apparent_encoding` when server omits `Content-Type` charset (preventing `£` -> `Â£` mojibake) and updated price regex to `([£$€]?[0-9]+[.,][0-9]{2})`.
- **Missing `sys` import (`main`)**: Imported `sys` so invalid `--site` arguments exit cleanly with code 1 instead of raising `NameError`.
- **Resume counter double-counting (`scrape_site`)**: Tracked per-page delta (`added - accounted`) instead of re-adding the cumulative run total on every page.
- **Multi-valued HTML attributes (`attr_text`)**: Normalised BeautifulSoup list/tuple attributes (such as `class`) into space-separated strings before regex or substring matching.
