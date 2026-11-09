# vmware-aiops-web: FastAPI frontend + session-pod image carrying the three CLIs
FROM python:3.12-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends nodejs npm curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*
# govc: full vSphere API surface (clone into pool/folder, relocate, tags…) that
# the curated vmware-aiops MCP toolset doesn't cover. Session pods get GOVC_*
# exported by the web layer from the per-session Secret.
RUN curl -fsSL https://github.com/vmware/govmomi/releases/download/v0.56.2/govc_Linux_x86_64.tar.gz \
 | tar -xz -C /usr/local/bin govc && govc version
RUN npm install -g @anthropic-ai/claude-code @google/gemini-cli @openai/codex
RUN pip install --no-cache-dir fastapi uvicorn itsdangerous pyVmomi kubernetes "vmware-aiops==2.4.6"
WORKDIR /app
COPY app/ ./app/
ENV DATA_DIR=/data \
    PORT=8080 \
    HOME=/data/home
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/healthz')"
ENTRYPOINT ["python", "app/main.py"]
