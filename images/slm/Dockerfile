# ==============================================================================
# hks-slm - a small language model server for CPU-only Kubernetes nodes.
#
# Ollama with the models already inside the image, so the pods need no internet
# access, no proxy settings and no persistent volume: containerd pulls the image
# once per node (through the node's proxy) and every pod starts with the models.
# Only the CPU parts of Ollama are kept: the official image is 8.5 GB, mostly
# GPU libraries the cluster does not have.
#
# Built by .github/workflows/images.yml and pushed to ghcr.io/<owner>/hks-slm:<VERSION>.
# Build by hand (repo root):
#   docker build -f images/slm/Dockerfile -t hks-slm:test .
# Behind a TLS-inspecting proxy, give the build that proxy's CA:
#   docker build --secret id=cacert,src=/path/ca.crt --build-arg HTTPS_PROXY=http://proxy:port ...
# Other models: --build-arg CHAT_MODEL=phi4-mini --build-arg EMBED_MODEL=nomic-embed-text
# ==============================================================================
ARG OLLAMA_VERSION=0.40.16

FROM ollama/ollama:${OLLAMA_VERSION} AS ollama

# --- download the models with the full image --------------------------------------
FROM ollama AS models
ARG CHAT_MODEL=granite4.1:3b
ARG EMBED_MODEL=granite-embedding:278m
ENV OLLAMA_MODELS=/models
RUN --mount=type=secret,id=cacert,required=false \
    set -eu; \
    if [ -f /run/secrets/cacert ]; then export SSL_CERT_FILE=/run/secrets/cacert; fi; \
    ollama serve >/tmp/serve.log 2>&1 & \
    for i in $(seq 1 60); do ollama list >/dev/null 2>&1 && break; sleep 1; done; \
    ollama pull "$CHAT_MODEL"; \
    ollama pull "$EMBED_MODEL"; \
    ollama list; \
    chmod -R a+rX /models

# --- keep only the CPU runtime -----------------------------------------------------
FROM ollama AS cpu
RUN mkdir -p /out/lib && cp -a /usr/bin/ollama /out/ollama && \
    find /usr/lib/ollama -maxdepth 1 \( -type f -o -type l \) -exec cp -a -t /out/lib {} +

FROM ubuntu:24.04
ARG CHAT_MODEL=granite4.1:3b
ARG EMBED_MODEL=granite-embedding:278m
ARG OLLAMA_VERSION=0.40.16
LABEL org.opencontainers.image.source="https://github.com/sahilanand07/hks-morpheus-apps" \
      org.opencontainers.image.description="Ollama ${OLLAMA_VERSION} (CPU only) with ${CHAT_MODEL} and ${EMBED_MODEL} built in" \
      org.opencontainers.image.licenses="MIT"
COPY --from=cpu /out/ollama /usr/bin/ollama
COPY --from=cpu /out/lib/ /usr/lib/ollama/
COPY --from=models /models /models
ENV OLLAMA_HOST=0.0.0.0:11434 \
    OLLAMA_MODELS=/models \
    OLLAMA_NOPRUNE=1 \
    OLLAMA_KEEP_ALIVE=-1 \
    OLLAMA_NO_CLOUD=1 \
    OLLAMA_CONTEXT_LENGTH=8192 \
    HOME=/tmp \
    CHAT_MODEL=${CHAT_MODEL} \
    EMBED_MODEL=${EMBED_MODEL}
USER 10002:10002
EXPOSE 11434
ENTRYPOINT ["/usr/bin/ollama"]
CMD ["serve"]
