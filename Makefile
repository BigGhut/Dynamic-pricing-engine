.PHONY: install test lint run-api run-dashboard run-local docker-build docker-up docker-down clean

POETRY = poetry

install:
	$(POETRY) install

test:
	$(POETRY) run pytest tests/

lint:
	$(POETRY) run ruff check src/ app/ tests/

run-api:
	$(POETRY) run python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000 --reload

run-dashboard:
	$(POETRY) run streamlit run app/dashboard.py

run-local:
	# Запуск API и Dashboard локально в фоне для Windows
	start /B $(POETRY) run python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000
	start /B $(POETRY) run streamlit run app/dashboard.py

docker-build:
	docker compose build

docker-up:
	docker compose up -d

docker-down:
	docker compose down

clean:
	rm -rf .pytest_cache .ruff_cache catboost_info
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
