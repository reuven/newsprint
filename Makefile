.PHONY: fixtures test lint

fixtures:
	uv run python scripts/make_fixtures.py

test:
	uv run pytest --cov=shabbat_print --cov-report=term-missing

lint:
	uv run ruff format src tests scripts
	uv run ruff check src tests scripts
