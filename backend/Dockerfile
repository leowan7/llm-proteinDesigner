FROM python:3.12-slim
WORKDIR /app

# Install Supabase CLI (D-06: Railway predeploy runs `supabase db push`).
# Pinned binary avoids npm runtime dependency in the container.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && curl -fsSL -o /tmp/supabase.deb https://github.com/supabase/cli/releases/download/v1.200.3/supabase_1.200.3_linux_amd64.deb \
 && dpkg -i /tmp/supabase.deb \
 && rm /tmp/supabase.deb \
 && apt-get purge -y curl \
 && rm -rf /var/lib/apt/lists/*

# Build context is repo root (Railway default). Explicit paths so docker-compose
# can also use `context: .` + `dockerfile: backend/Dockerfile`.
# supabase/ migrations are copied in because railway.toml preDeployCommand
# runs `supabase db push` from /app/ before traffic shifts.
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ /app/
COPY supabase/ /app/supabase/
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
