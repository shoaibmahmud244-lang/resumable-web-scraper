.PHONY: install lint typecheck test run

install:
	python3 -m pip install -e ".[dev]"

lint:
	ruff check .

typecheck:
	mypy scraper.py tests/

test:
	pytest -v

run:
	python3 scraper.py --site quotes --fresh
