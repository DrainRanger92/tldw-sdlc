FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g opencode-ai@1.17.18 \
    && rm -rf /var/lib/apt/lists/*
COPY orchestrator /opt/aidevteam-pkg
RUN pip install --no-cache-dir "/opt/aidevteam-pkg[dev]"
# Репозиторий смонтирован снаружи и принадлежит другому пользователю —
# без этого git отказывается работать: "detected dubious ownership".
RUN git config --global --add safe.directory /workspace
WORKDIR /workspace
