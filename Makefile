.PHONY: help run test check

PYTHON ?= python3

help:
	@echo "KITT UI"
	@echo "  make run    Run local UI at http://127.0.0.1:8776"
	@echo "  make test   Run unit tests"
	@echo "  make check  Compile and test"

run:
	$(PYTHON) server.py

test:
	$(PYTHON) -m unittest discover -s tests

check:
	$(PYTHON) -B -m py_compile server.py kitt_runtime_client.py
	$(MAKE) test
