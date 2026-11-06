# vmware-aiops-web: FastAPI frontend + session-pod image carrying the three CLIs
FROM python:3.12-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends nodejs npm \
 && rm -rf /var/lib/apt/lists/*
RUN npm install -g @anthropic-ai/claude-code @google/gemini-cli @openai/codex
RUN pip install --no-cache-dir fastapi uvicorn itsdangerous pyVmomi kubernetes "vmware-aiops==2.4.2"
WORKDIR /app
COPY app/ ./app/
ENV DATA_DIR=/data \
    PORT=8080 \
    HOME=/data/home
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/healthz')"
ENTRYPOINT ["python", "app/main.py"]
