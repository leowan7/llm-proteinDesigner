#!/usr/bin/env bash
set -e

echo "Starting Supabase local stack..."
npx supabase start

echo ""
echo "===== Supabase Status ====="
npx supabase status
echo "==========================="
echo ""
echo "IMPORTANT: Copy the ANON KEY, SERVICE ROLE KEY, and JWT SECRET above into .env.local"
echo ""

echo "Starting Docker Compose services..."
docker compose up -d

echo "Waiting for MinIO to be ready..."
sleep 3

echo "Creating MinIO bucket (idempotent)..."
docker compose exec minio mc alias set local http://localhost:9000 minioadmin minioadmin 2>/dev/null || true
docker compose exec minio mc mb --ignore-existing local/protein-designer 2>/dev/null || true

echo ""
echo "Dev environment ready."
echo "  Supabase Studio:  http://localhost:54323"
echo "  Inbucket (email): http://localhost:54324"
echo "  FastAPI:          http://localhost:8000"
echo "  MinIO console:    http://localhost:9001"
echo ""
echo "  Test user: test@example.com / Password123!"
