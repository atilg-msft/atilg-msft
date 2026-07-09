# --- builder: compile wheels that need a toolchain (py-clob-client's crypto deps) ---
FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libssl-dev \
        libffi-dev \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY polybot ./polybot

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir ".[web,azure,live]"

# --- runtime: slim image, no compiler ---
FROM python:3.11-slim

RUN useradd --create-home --uid 10001 polybot
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    POLYBOT_DATA_DIR=/app/data

COPY --from=builder /opt/venv /opt/venv

RUN mkdir -p /app/data && chown -R polybot:polybot /app
USER polybot

EXPOSE 8000
CMD ["polybot-server"]
