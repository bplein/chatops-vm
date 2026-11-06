# vmware-aiops-web: FastAPI chat UI wrapping claude-agent-sdk + the vmware-aiops MCP server.
FROM python:3.12-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends nodejs npm \
 && rm -rf /var/lib/apt/lists/*
RUN npm install -g @anthropic-ai/claude-code
RUN pip install --no-cache-dir fastapi uvicorn itsdangerous pyVmomi claude-agent-sdk "vmware-aiops==2.4.2"
WORKDIR /app
COPY app/main.py ./app/main.py
ENV DATA_DIR=/data \
    PORT=8080 \
    HOME=/data/home
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/healthz')"
ENTRYPOINT ["python", "app/main.py"]
