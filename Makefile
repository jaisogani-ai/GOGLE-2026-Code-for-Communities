.PHONY: install dev test api eval scorer docker-build clean

PYTHON ?= .venv/bin/python

install: ; $(PYTHON) -m pip install -r requirements-dev.txt
# Local server on loopback; reads .env (git-ignored) at startup.
api:     ; $(PYTHON) -m uvicorn api.main:app --reload --host 127.0.0.1 --port 8000
test:    ; $(PYTHON) -m pytest tests/ -q
# Offline SYNTHETIC benchmark and trust-scorer evaluation (never served by the app).
eval:    ; $(PYTHON) -m tathyon.evaluate
scorer:  ; $(PYTHON) -m tathyon.trust_eval
docker-build: ; docker build -t tathyon .
clean:   ; rm -rf __pycache__ .pytest_cache
