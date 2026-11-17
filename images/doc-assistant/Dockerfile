# ==============================================================================
# hks-doc-assistant - the web app of the Private AI Assistant (FastAPI).
# It finds the passages that answer a question and has the model server
# (images/slm) write the answer. The repo's own docs are its built-in knowledge.
#
# Built by .github/workflows/images.yml -> ghcr.io/<owner>/hks-doc-assistant:<VERSION>.
# Build by hand from the repo root:
#   docker build -f images/doc-assistant/Dockerfile -t hks-doc-assistant:test .
# Behind a TLS-inspecting proxy add: --secret id=cacert,src=/path/ca.crt
# ==============================================================================
FROM python:3.13-slim
LABEL org.opencontainers.image.source="https://github.com/sahilanand07/hks-morpheus-apps" \
      org.opencontainers.image.description="Private AI Assistant web app (RAG over documents, small language model on Kubernetes)" \
      org.opencontainers.image.licenses="MIT"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY images/doc-assistant/requirements.txt .
RUN --mount=type=secret,id=cacert,required=false \
    if [ -f /run/secrets/cacert ]; then export PIP_CERT=/run/secrets/cacert; fi; \
    pip install -r requirements.txt
COPY images/doc-assistant/app/ /app/app/
COPY images/doc-assistant/VERSION /app/VERSION
COPY images/doc-assistant/knowledge/ /app/knowledge/
COPY PLAYBOOK.md /app/knowledge/hks-morpheus-apps-playbook.md
COPY README.md /app/knowledge/hks-morpheus-apps-readme.md
USER 10001:10001
EXPOSE 8080
WORKDIR /app/app
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--timeout-graceful-shutdown", "20"]
