.PHONY: contract backend packages web e2e terraform-fmt terraform-init terraform-validate \
	terraform-test terraform-plan-guard terraform-python terraform-lint terraform-scan check-local

PYTHON ?= python3
TERRAFORM ?= terraform
TFLINT ?= tflint
CHECKOV ?= checkov
NPM ?= npm

contract:
	PYTHONPATH=.. PYTHONDONTWRITEBYTECODE=1 $(PYTHON) scripts/validate_candidate.py
	PYTHONPATH=.. PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m unittest discover -s tests -p 'test_*.py' -v

backend:
	PYTHONPATH=.. PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m pytest tests/backend -q

packages:
	PYTHONPATH=.. PYTHONDONTWRITEBYTECODE=1 $(PYTHON) scripts/build_lambda_packages.py build
	PYTHONPATH=.. PYTHONDONTWRITEBYTECODE=1 $(PYTHON) scripts/build_lambda_packages.py verify

web:
	$(NPM) --prefix web run check
	$(NPM) --prefix web audit --audit-level=high

e2e:
	$(NPM) --prefix web run test:e2e

terraform-fmt:
	$(TERRAFORM) fmt -check -recursive terraform

terraform-init:
	$(TERRAFORM) -chdir=terraform init -backend=false -input=false

terraform-validate: terraform-init
	$(TERRAFORM) -chdir=terraform validate

terraform-test: terraform-init
	$(TERRAFORM) -chdir=terraform test

terraform-plan-guard:
	PYTHONPATH=.. PYTHONDONTWRITEBYTECODE=1 $(PYTHON) terraform/plan_guard.py --static-only

terraform-python:
	PYTHONPATH=.. PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m unittest discover -s terraform/tests -p 'test_*.py' -v

terraform-lint:
	$(TFLINT) --chdir=terraform --config=../../.tflint.hcl

terraform-scan:
	$(CHECKOV) --directory terraform --framework terraform --quiet --compact --skip-download \
		--skip-path terraform/.terraform --skip-path terraform/tests

check-local: contract backend packages web e2e terraform-fmt terraform-validate \
	terraform-test terraform-plan-guard terraform-python terraform-lint terraform-scan
