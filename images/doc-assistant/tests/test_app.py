"""Tests for the Private AI Assistant web app, against a stand-in for Ollama.

The stand-in answers /api/tags, /api/embed (word-hash vectors, so similar text gets
similar vectors) and /api/chat (a short streamed answer), so CI can test the app
without downloading the models.

  pip install -r images/doc-assistant/requirements.txt pytest
  pytest -q images/doc-assistant/tests
"""
import hashlib
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pytest

pytestmark = pytest.mark.filterwarnings("ignore:Using `httpx`")

HERE = Path(__file__).resolve().parent
APP_DIR = HERE.parent / "app"
DIM = 64


def fake_vector(text):
    v = np.zeros(DIM)
    for w in text.lower().split():
        v[int(hashlib.md5(w.encode()).hexdigest(), 16) % DIM] += 1
    return v.tolist()


class FakeOllama(BaseHTTPRequestHandler):
    chats = []

    def log_message(self, *args):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/tags":
            self._json({"models": [{"name": "granite4.1:3b"}, {"name": "granite-embedding:278m"}]})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/api/embed":
            self._json({"embeddings": [fake_vector(t) for t in req["input"]]})
        elif self.path == "/api/chat":
            FakeOllama.chats.append(req)
            if not req.get("stream"):
                self._json({"message": {"role": "assistant", "content": "ok"}, "done": True})
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
            for tok in ["The answer ", "is in ", "the docs [1]."]:
                self.wfile.write((json.dumps({"message": {"content": tok}, "done": False}) + "\n").encode())
                self.wfile.flush()
            self.wfile.write((json.dumps({"message": {"content": ""}, "done": True, "eval_count": 3,
                                          "eval_duration": 300_000_000, "prompt_eval_count": 50}) + "\n").encode())
        else:
            self._json({"error": "not found"}, 404)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    kb = tmp_path_factory.mktemp("knowledge")
    (kb / "guide.md").write_text(
        "# Guide\n\nIntro text about the platform.\n\n## Calico\n\n"
        "Calico showed Gateway Timeout because NO_PROXY missed the pod range.\n\n"
        "```bash\n# not a heading\necho hi\n```\n\n## Rollback\n\nUse git revert, then run the task again.\n")
    os.environ.update(OLLAMA_URL=f"http://127.0.0.1:{srv.server_address[1]}", KNOWLEDGE_DIR=str(kb),
                      APP_VERSION="9.9.9", POD_NAME="test-pod", NODE_NAME="test-node")
    sys.path.insert(0, str(APP_DIR))
    import main  # noqa: PLC0415 - must import after the environment is set
    from fastapi.testclient import TestClient

    with TestClient(main.app) as c:
        for _ in range(100):
            if c.get("/api/status").json()["warm"]:
                break
            time.sleep(0.1)
        yield c
    srv.shutdown()


def events(text):
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_status_ready(client):
    s = client.get("/api/status").json()
    assert s["slm"] == "ready" and s["index"] == "ready" and s["warm"]
    assert s["documents"] == 1 and s["chunks"] >= 2
    assert s["version"] == "9.9.9" and s["pod"] == "test-pod"


def test_page_has_pod_and_version(client):
    r = client.get("/")
    assert r.status_code == 200
    assert 'id="pod">test-pod<' in r.text and 'id="version">9.9.9<' in r.text
    assert "{{" not in r.text


def test_ask_streams_sources_tokens_done(client):
    r = client.post("/api/ask", json={"question": "Why Gateway Timeout in Calico?"})
    assert r.status_code == 200
    ev = events(r.text)
    assert ev[0][0] == "sources" and ev[0][1][0]["section"].endswith("Calico")
    assert "".join(d["t"] for e, d in ev if e == "token") == "The answer is in the docs [1]."
    done = [d for e, d in ev if e == "done"][0]
    assert done["tokens"] == 3 and done["tokens_per_s"] == 10.0 and done["pod"] == "test-pod"
    prompt = FakeOllama.chats[-1]["messages"][1]["content"]
    assert prompt.startswith("Sources:") and "Question: Why Gateway Timeout" in prompt


def test_ask_without_documents(client):
    ev = events(client.post("/api/ask", json={"question": "hello", "use_docs": False}).text)
    assert ev[0] == ("sources", [])
    assert len(FakeOllama.chats[-1]["messages"]) == 2


def test_empty_question_rejected(client):
    assert client.post("/api/ask", json={"question": "  "}).status_code == 400


def test_upload_list_delete(client):
    r = client.post("/api/documents", files={"file": ("plant.md", b"# Plant\n\nLine 4 torque check every 14 days.")})
    assert r.status_code == 200 and r.json()["chunks"] == 1
    names = {d["name"]: d["source"] for d in client.get("/api/documents").json()}
    assert names == {"guide.md": "built-in", "plant.md": "uploaded"}
    ev = events(client.post("/api/ask", json={"question": "torque check line 4"}).text)
    assert ev[0][1][0]["doc"] == "plant.md"
    assert client.delete("/api/documents/plant.md").status_code == 200
    assert client.delete("/api/documents/guide.md").status_code == 403
    assert client.delete("/api/documents/nope.md").status_code == 404


def test_upload_rejects_other_types(client):
    r = client.post("/api/documents", files={"file": ("tool.exe", b"MZ")})
    assert r.status_code == 415 and "error" in r.json()


def test_markdown_sections_and_chunks():
    import rag

    secs = rag.read_markdown("# A\n\ntext a\n\n```\n# code, not a heading\n```\n\n## B\n\ntext b\n")
    assert [s for s, _ in secs] == ["A", "A > B"]
    assert "# code, not a heading" in secs[0][1]
    long = "\n\n".join(f"Paragraph {i} " + "word " * 60 for i in range(10))
    chunks = rag.make_chunks("d.md", [("S", long)])
    assert len(chunks) > 3 and all(len(c.text) <= rag.CHUNK_CHARS + 2 for c in chunks)
