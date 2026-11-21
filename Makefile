# Shortcuts for running Dori locally: `make install` once, then `make run`
# and `make tunnel` in two terminals.

PYTHON := .venv/bin/python

.PHONY: install run tunnel test

install:
	python3 -m venv .venv
	$(PYTHON) -m pip install -r requirements-dev.txt

run:
	$(PYTHON) -m uvicorn dori.server:app --port 8000

tunnel:
	ngrok http --url=$$($(PYTHON) -c "from dori import config; print(config.NGROK_DOMAIN)") 8000

test:
	$(PYTHON) -m pytest
