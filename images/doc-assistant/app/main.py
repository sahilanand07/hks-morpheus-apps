"""Private AI Assistant: ask questions about documents, answered by a small language model
that runs inside the Kubernetes cluster (Ollama, see images/slm).

  GET  /                    the web page
  POST /api/ask             {"question": "...", "use_docs": true}  -> server-sent events
  GET  /api/status          model server, models, index, pod and node
  GET  /api/documents       the knowledge base
  POST /api/documents       upload a .pdf, .md or .txt (multipart field "file")
  DELETE /api/documents/X   remove an uploaded document
  GET  /healthz             liveness / readiness
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import socket
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

import rag

HERE = Path(__file__).resolve().parent
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://doc-assistant-slm:11434").rstrip("/")
CHAT_MODEL = os.environ.get("CHAT_MODEL", "granite4.1:3b")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "granite-embedding:278m")
KNOWLEDGE_DIR = Path(os.environ.get("KNOWLEDGE_DIR", HERE.parent / "knowledge"))
VERSION_FILE = HERE.parent / "VERSION"
APP_VERSION = os.environ.get("APP_VERSION") or (VERSION_FILE.read_text().strip() if VERSION_FILE.exists() else "dev")
POD = os.environ.get("POD_NAME") or socket.gethostname()
NODE = os.environ.get("NODE_NAME", "unknown")
TOP_K = int(os.environ.get("TOP_K", "4"))
NUM_CTX = int(os.environ.get("NUM_CTX", "8192"))
NUM_PREDICT = int(os.environ.get("NUM_PREDICT", "768"))
NUM_THREAD = int(os.environ.get("NUM_THREAD", "0"))        # 0 = let Ollama decide
TEMPERATURE = float(os.environ.get("TEMPERATURE", "0.2"))
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "20"))
MAX_DOCS = int(os.environ.get("MAX_UPLOADED_DOCS", "20"))
EMBED_BATCH = 16

log = logging.getLogger("assistant")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)


class _QuietPolls(logging.Filter):
    """Keep the browser's status polling and the probes out of the access log."""
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(p in msg for p in ('"GET /api/status', '"GET /healthz', '"GET /api/documents'))


logging.getLogger("uvicorn.access").addFilter(_QuietPolls())

index = rag.Index()
index_lock = asyncio.Lock()
state = {"slm": "starting", "index": "waiting for the model server", "warm": False, "detail": ""}
client: httpx.AsyncClient


# --- model server calls ---------------------------------------------------------------------
async def embed(texts: list[str]) -> np.ndarray:
    out = []
    for i in range(0, len(texts), EMBED_BATCH):
        r = await client.post(f"{OLLAMA_URL}/api/embed",
                              json={"model": EMBED_MODEL, "input": texts[i:i + EMBED_BATCH], "truncate": True},
                              timeout=300)
        r.raise_for_status()
        out.extend(r.json()["embeddings"])
    return np.array(out, dtype=np.float32)


def chat_options() -> dict:
    opts = {"num_ctx": NUM_CTX, "num_predict": NUM_PREDICT, "temperature": TEMPERATURE}
    if NUM_THREAD > 0:
        opts["num_thread"] = NUM_THREAD
    return opts


async def ensure_models() -> None:
    """Wait for Ollama; pull a model only if the image does not already contain it."""
    while True:
        try:
            r = await client.get(f"{OLLAMA_URL}/api/tags", timeout=10)
            r.raise_for_status()
            have = {m["name"] for m in r.json().get("models", [])}
            break
        except Exception as exc:  # noqa: BLE001 - keep retrying, show why
            state.update(slm="unreachable", detail=f"{OLLAMA_URL}: {type(exc).__name__}")
            await asyncio.sleep(5)
    for model in (EMBED_MODEL, CHAT_MODEL):
        if model in have or f"{model}:latest" in have:
            continue
        state.update(slm="downloading", detail=f"pulling {model}")
        log.info("model %s not in the server, pulling it", model)
        async with client.stream("POST", f"{OLLAMA_URL}/api/pull", json={"model": model}, timeout=None) as r:
            async for line in r.aiter_lines():
                if line:
                    msg = json.loads(line)
                    if msg.get("total"):
                        state["detail"] = f"pulling {model}: {100 * msg.get('completed', 0) // msg['total']}%"
                    if msg.get("error"):
                        raise RuntimeError(msg["error"])
    state.update(slm="ready", detail="")


async def load_knowledge() -> None:
    files = sorted(p for p in KNOWLEDGE_DIR.glob("*") if p.suffix.lower() in (".md", ".txt", ".pdf"))
    state["index"] = f"embedding {len(files)} built-in documents"
    t0 = time.monotonic()
    for p in files:
        try:
            await add_document(p.name, p.read_bytes(), "built-in")
        except HTTPException as exc:          # e.g. a PDF without a text layer: skip it, keep going
            log.warning("skipped %s: %s", p.name, exc.detail)
    state["index"] = "ready"
    log.info("knowledge base ready: %d documents, %d chunks in %.1fs",
             len(index.docs), len(index.chunks), time.monotonic() - t0)


async def warm_up() -> None:
    """Load the chat model into memory so the first question does not wait for it."""
    t0 = time.monotonic()
    r = await client.post(f"{OLLAMA_URL}/api/chat", timeout=600, json={
        "model": CHAT_MODEL, "messages": [{"role": "user", "content": "Hi"}],
        "stream": False, "keep_alive": -1, "options": {**chat_options(), "num_predict": 1}})
    r.raise_for_status()
    state["warm"] = True
    log.info("chat model %s loaded in %.1fs", CHAT_MODEL, time.monotonic() - t0)


async def startup() -> None:
    while True:
        try:
            await ensure_models()
            await load_knowledge()
            await warm_up()
            return
        except Exception as exc:  # noqa: BLE001
            log.warning("startup step failed, retrying in 10s: %s", exc)
            state["detail"] = f"{type(exc).__name__}: {exc}"[:200]
            await asyncio.sleep(10)


async def add_document(name: str, data: bytes, source: str) -> rag.Document:
    sections = await asyncio.to_thread(rag.read_file, name, data)
    chunks = rag.make_chunks(name, sections)
    if not chunks:
        raise HTTPException(422, f"no text found in {name} (a scanned PDF has no text layer)")
    vectors = await embed([rag.embed_text(c) for c in chunks])
    doc = rag.Document(name=name, source=source, chars=sum(len(c.text) for c in chunks))
    async with index_lock:
        index.add(doc, chunks, vectors)
    log.info("indexed %s (%s): %d chunks", name, source, len(chunks))
    return doc


@asynccontextmanager
async def lifespan(_: FastAPI):
    global client
    client = httpx.AsyncClient(trust_env=False)      # in-cluster only: never use a proxy
    task = asyncio.create_task(startup())
    yield
    task.cancel()
    await client.aclose()


app = FastAPI(title="Private AI Assistant", version=APP_VERSION, lifespan=lifespan)


# --- page and status ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def page() -> str:
    tpl = (HERE / "static" / "index.html").read_text(encoding="utf-8")
    for key, value in {"POD": POD, "NODE": NODE, "VERSION": APP_VERSION,
                       "CHAT_MODEL": CHAT_MODEL, "EMBED_MODEL": EMBED_MODEL}.items():
        tpl = tpl.replace("{{" + key + "}}", html.escape(value))
    return tpl


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


@app.get("/api/status")
async def status() -> dict:
    return {
        "version": APP_VERSION, "pod": POD, "node": NODE,
        "chat_model": CHAT_MODEL, "embed_model": EMBED_MODEL, "model_server": OLLAMA_URL,
        "slm": state["slm"], "index": state["index"], "warm": state["warm"], "detail": state["detail"],
        "documents": len(index.docs), "chunks": len(index.chunks),
    }


@app.get("/api/documents")
async def documents() -> list[dict]:
    return [{"name": d.name, "source": d.source, "chunks": d.chunks, "chars": d.chars}
            for d in sorted(index.docs.values(), key=lambda d: (d.source != "built-in", d.name.lower()))]


@app.post("/api/documents")
async def upload(file: UploadFile = File(...)) -> dict:
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", Path(file.filename or "upload").name)[:120]
    if Path(name).suffix.lower() not in (".pdf", ".md", ".markdown", ".txt"):
        raise HTTPException(415, "upload a .pdf, .md or .txt file")
    if state["index"] != "ready":
        raise HTTPException(503, "the knowledge base is still loading, try again in a moment")
    data = await file.read(MAX_UPLOAD_MB * 1024 * 1024 + 1)
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"file is larger than {MAX_UPLOAD_MB} MB")
    if sum(d.source == "uploaded" for d in index.docs.values()) >= MAX_DOCS and name not in index.docs:
        raise HTTPException(409, f"at most {MAX_DOCS} uploaded documents; remove one first")
    if index.docs.get(name) and index.docs[name].source == "built-in":
        raise HTTPException(409, f"{name} is a built-in document; rename the file")
    t0 = time.monotonic()
    doc = await add_document(name, data, "uploaded")
    return {"name": doc.name, "chunks": doc.chunks, "seconds": round(time.monotonic() - t0, 1)}


@app.delete("/api/documents/{name}")
async def remove(name: str) -> dict:
    doc = index.docs.get(name)
    if not doc:
        raise HTTPException(404, "no such document")
    if doc.source == "built-in":
        raise HTTPException(403, "built-in documents come from the image; change them in Git")
    async with index_lock:
        index.remove(name)
    return {"removed": name}


# --- questions -------------------------------------------------------------------------------------
class Ask(BaseModel):
    question: str
    use_docs: bool = True


SYSTEM_DOCS = (
    "You are the Private AI Assistant. You run on a small language model inside the customer's own "
    "Kubernetes cluster, so no data leaves it. The user gives you numbered sources and a question. "
    "Answer only from the sources. Start with the answer itself: do not repeat the source titles, and do "
    "not add a list of sources at the end. Put the source number in square brackets right after each fact "
    "it supports, like [2]. If the sources do not contain the answer, say that the documents do not cover "
    "it. Be concise and practical: short paragraphs, numbered steps for procedures, commands in code blocks."
)
SYSTEM_PLAIN = (
    "You are the Private AI Assistant, a small language model running inside the customer's own "
    "Kubernetes cluster. Answer concisely. If you are not sure, say so."
)


def sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.post("/api/ask")
async def ask(req: Ask) -> StreamingResponse:
    question = req.question.strip()[:2000]
    if not question:
        raise HTTPException(400, "empty question")
    if state["slm"] != "ready":
        raise HTTPException(503, f"model server not ready: {state['slm']} {state['detail']}".strip())
    if req.use_docs and state["index"] != "ready":
        raise HTTPException(503, f"knowledge base not ready: {state['index']}")

    async def stream():
        t0 = time.monotonic()
        sources = []
        if req.use_docs:
            qvec = (await embed([question]))[0]
            hits = index.search(qvec, question, TOP_K)
            blocks = []
            for n, (chunk, score) in enumerate(hits, 1):
                title = f"{chunk.doc} - {chunk.section}" if chunk.section else chunk.doc
                blocks.append(f"[{n}] ({title})\n{chunk.text}")
                sources.append({"n": n, "doc": chunk.doc, "section": chunk.section,
                                "score": round(score, 3), "text": chunk.text})
            messages = [{"role": "system", "content": SYSTEM_DOCS},
                        {"role": "user", "content": "Sources:\n\n" + "\n\n".join(blocks) + "\n\nQuestion: " + question}]
        else:
            messages = [{"role": "system", "content": SYSTEM_PLAIN}, {"role": "user", "content": question}]
        yield sse("sources", sources)
        retrieval_s = time.monotonic() - t0

        first = None
        try:
            async with client.stream("POST", f"{OLLAMA_URL}/api/chat", timeout=httpx.Timeout(600, connect=10), json={
                    "model": CHAT_MODEL, "messages": messages, "stream": True,
                    "keep_alive": -1, "options": chat_options()}) as r:
                if r.status_code != 200:
                    body = (await r.aread()).decode(errors="replace")[:300]
                    yield sse("error", {"message": f"model server answered {r.status_code}: {body}"})
                    return
                async for line in r.aiter_lines():
                    if not line:
                        continue
                    msg = json.loads(line)
                    if msg.get("error"):
                        yield sse("error", {"message": msg["error"]})
                        return
                    token = (msg.get("message") or {}).get("content", "")
                    if token:
                        if first is None:
                            first = time.monotonic() - t0
                        yield sse("token", {"t": token})
                    if msg.get("done"):
                        ev, ed = msg.get("eval_count", 0), msg.get("eval_duration", 0) or 1
                        yield sse("done", {
                            "tokens": ev, "tokens_per_s": round(ev / (ed / 1e9), 1),
                            "prompt_tokens": msg.get("prompt_eval_count", 0),
                            "first_token_s": round(first or 0, 2), "retrieval_s": round(retrieval_s, 2),
                            "total_s": round(time.monotonic() - t0, 2),
                            "model": CHAT_MODEL, "pod": POD, "node": NODE})
        except httpx.HTTPError as exc:
            yield sse("error", {"message": f"model server error: {type(exc).__name__} {exc}"})

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.exception_handler(HTTPException)
async def http_error(_, exc: HTTPException) -> JSONResponse:
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
