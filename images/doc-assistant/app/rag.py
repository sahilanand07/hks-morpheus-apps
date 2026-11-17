"""Document handling for the assistant: read files, cut them into chunks, embed, search.

Everything lives in memory. The built-in documents are embedded again when a pod
starts (a few seconds on CPU); uploaded documents stay in the pod that received
them until it restarts.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

CHUNK_CHARS = 900          # target chunk size: a few paragraphs
MIN_CHUNK_CHARS = 120      # tiny trailing pieces are merged into the previous chunk
STOPWORDS = set(
    "a an and are as at be but by can do does for from how i if in into is it its me my "
    "of on or our so than that the their them then there these this to us was we what "
    "when where which who why will with you your".split()
)


@dataclass
class Chunk:
    doc: str
    section: str
    text: str


@dataclass
class Document:
    name: str
    source: str            # "built-in" or "uploaded"
    chars: int
    chunks: int = 0


@dataclass
class Index:
    chunks: list[Chunk] = field(default_factory=list)
    vectors: np.ndarray | None = None
    docs: dict[str, Document] = field(default_factory=dict)

    def add(self, doc: Document, chunks: list[Chunk], vectors: np.ndarray) -> None:
        self.remove(doc.name)
        doc.chunks = len(chunks)
        self.docs[doc.name] = doc
        self.chunks.extend(chunks)
        vectors = normalize(vectors)
        self.vectors = vectors if self.vectors is None else np.vstack([self.vectors, vectors])

    def remove(self, name: str) -> bool:
        if name not in self.docs:
            return False
        keep = [i for i, c in enumerate(self.chunks) if c.doc != name]
        self.chunks = [self.chunks[i] for i in keep]
        self.vectors = self.vectors[keep] if self.vectors is not None and keep else None
        del self.docs[name]
        return True

    def search(self, query_vec: np.ndarray, question: str, k: int) -> list[tuple[Chunk, float]]:
        """Cosine similarity plus a small bonus for exact word matches (error messages, names)."""
        if self.vectors is None or not self.chunks:
            return []
        q = normalize(query_vec.reshape(1, -1))[0]
        scores = self.vectors @ q
        words = keywords(question)
        if words:
            bonus = np.array([sum(w in c.text.lower() for w in words) / len(words) for c in self.chunks])
            scores = scores + 0.15 * bonus
        order = np.argsort(-scores)[:k]
        return [(self.chunks[i], float(scores[i])) for i in order]


def normalize(m: np.ndarray) -> np.ndarray:
    m = np.asarray(m, dtype=np.float32)
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return m / norms


def keywords(text: str) -> list[str]:
    return sorted({w for w in re.findall(r"[a-z0-9][a-z0-9_.:/-]{2,}", text.lower()) if w not in STOPWORDS})


# --- reading files ---------------------------------------------------------------------
def read_file(name: str, data: bytes) -> list[tuple[str, str]]:
    """Return (section, text) pairs for a .md, .txt or .pdf file."""
    suffix = Path(name).suffix.lower()
    if suffix == ".pdf":
        return read_pdf(data)
    text = data.decode("utf-8", errors="replace")
    if suffix in (".md", ".markdown"):
        return read_markdown(text)
    return [("", text)]


def read_markdown(text: str) -> list[tuple[str, str]]:
    """Split on headings; each section remembers its heading path (e.g. 'Phase 2 > Step 3')."""
    sections, path, buf = [], [], []
    in_code = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
        m = None if in_code else re.match(r"^(#{1,4})\s+(.*\S)\s*$", line)
        if m:
            if buf and "".join(buf).strip():
                sections.append((" > ".join(path), "\n".join(buf).strip()))
            buf = []
            level = len(m.group(1))
            path = path[: level - 1] + [re.sub(r"[*`]", "", m.group(2))]
        else:
            buf.append(line)
    if buf and "".join(buf).strip():
        sections.append((" > ".join(path), "\n".join(buf).strip()))
    return sections


def read_pdf(data: bytes) -> list[tuple[str, str]]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    out = []
    for n, page in enumerate(reader.pages, 1):
        text = (page.extract_text() or "").strip()
        if text:
            out.append((f"page {n}", text))
    return out


# --- chunking -----------------------------------------------------------------------------
def make_chunks(doc: str, sections: list[tuple[str, str]]) -> list[Chunk]:
    chunks: list[Chunk] = []
    for section, text in sections:
        paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        buf = ""
        for p in paras:
            for piece in split_long(p):
                if buf and len(buf) + len(piece) + 2 > CHUNK_CHARS:
                    chunks.append(Chunk(doc, section, buf))
                    buf = ""
                buf = f"{buf}\n\n{piece}" if buf else piece
        if buf:
            if len(buf) < MIN_CHUNK_CHARS and chunks and chunks[-1].section == section:
                chunks[-1].text += "\n\n" + buf
            else:
                chunks.append(Chunk(doc, section, buf))
    return chunks


def split_long(p: str) -> list[str]:
    if len(p) <= CHUNK_CHARS:
        return [p]
    sentences = re.split(r"(?<=[.!?])\s+|\n", p)
    out, buf = [], ""
    for s in sentences:
        while len(s) > CHUNK_CHARS:            # a very long line (table row, log line)
            out.append(s[:CHUNK_CHARS])
            s = s[CHUNK_CHARS:]
        if buf and len(buf) + len(s) + 1 > CHUNK_CHARS:
            out.append(buf)
            buf = ""
        buf = f"{buf} {s}".strip()
    if buf:
        out.append(buf)
    return out


def embed_text(c: Chunk) -> str:
    """What gets embedded: the chunk with its document and section, so headings count too."""
    head = f"{c.doc} - {c.section}" if c.section else c.doc
    return f"{head}\n{c.text}"
