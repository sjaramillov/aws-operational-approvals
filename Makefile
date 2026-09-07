.PHONY: demo demo-api check-local python-check web-check e2e smoke-api packages terraform-static
PYTHON ?= python3
NPM ?= npm
TERRAFORM ?= terraform

demo:
	$(NPM) --prefix sales_demo/web run dev

demo-api:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m sales_demo.backend.local_server --port 8081

python-check:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) sales_demo/scripts/validate_candidate.py
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m pytest sales_demo/tests sales_demo/terraform/tests -q

web-check:
	$(NPM) --prefix sales_demo/web run check

e2e:
	$(NPM) --prefix sales_demo/web run test:e2e

smoke-api:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) scripts/smoke_local_api.py

packages:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) sales_demo/scripts/build_lambda_packages.py build
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) sales_demo/scripts/build_lambda_packages.py verify

terraform-static:
	$(TERRAFORM) fmt -check -recursive sales_demo/terraform
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) sales_demo/terraform/plan_guard.py --static-only

check-local: python-check web-check e2e smoke-api packages terraform-static
