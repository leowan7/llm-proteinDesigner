.PHONY: dev stop reset

dev:
	@bash scripts/dev-up.sh

stop:
	@echo "Stopping Docker Compose services..."
	@docker compose down
	@echo "Stopping Supabase..."
	@npx supabase stop

reset:
	@echo "Resetting Supabase database (re-applies migrations + seed)..."
	@npx supabase db reset
