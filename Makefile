.PHONY: install test lint run worker migrate seed portal website up evals
install:
	cd backend && pip install -e ".[dev,temporal]"
test:
	cd backend && python -m pytest -q
lint:
	cd backend && ruff check src tests
run:
	cd backend && uvicorn advisorgro.api.main:app_factory --factory --reload --port 8000
worker:
	cd backend && python -m advisorgro.workflows.worker
migrate:
	cd backend && alembic upgrade head
seed:
	cd backend && python ../scripts/seed_dev.py
portal:
	cd portal && npm install && npm run dev
website:
	cd website && python -m http.server 8081
up:
	docker compose up --build
evals:
	cd evals && npx promptfoo@latest eval -c promptfooconfig.yaml
