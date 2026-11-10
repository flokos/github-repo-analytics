# Convenience wrappers around the documented `docker compose` commands.
.DEFAULT_GOAL := help
.PHONY: help env up down ps logs clean test test-backend test-airflow trigger

help: ## List the available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "} {printf "  %-16s %s\n", $$1, $$2}'

env: ## Create .env with generated secrets (no-op if it already exists)
	./scripts/bootstrap-env.sh

up: env ## Build and start the whole stack in the background
	docker compose up --build --detach

down: ## Stop the stack and keep the data volumes
	docker compose down

ps: ## Show service status and health
	docker compose ps

logs: ## Follow the logs of every service
	docker compose logs --follow --tail 100

test: test-backend test-airflow ## Run every test suite in containers

test-backend: env ## Lint and test the Django backend
	docker compose --profile test run --rm --build backend-tests

test-airflow: ## Lint and test the Airflow DAG
	docker compose --profile test run --rm --build airflow-tests

trigger: ## Start a pipeline run now instead of waiting for the schedule
	docker compose exec airflow-scheduler airflow dags trigger github_metrics_daily

clean: ## Stop the stack and delete all data volumes
	docker compose down --volumes --remove-orphans
