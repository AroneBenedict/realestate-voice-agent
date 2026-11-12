.PHONY: install test lint evaluate serve

install:
	pip install -e ".[dev]"

lint:
	ruff check .

test:
	pytest --cov --cov-report=term-missing

evaluate:
	python -m voiceagent.evaluate --calls 1000 --out results

serve:
	uvicorn voiceagent.api:app --port 8000
