FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm git gh \
    && npm install -g @anthropic-ai/claude-code \
    && rm -rf /var/lib/apt/lists/*
RUN git config --system credential.helper "!gh auth git-credential"
COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /uvx /bin/
WORKDIR /srv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY app ./app
EXPOSE 8001
CMD ["uv", "run", "--no-sync", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
