"""
RAG engine - Retrieval Augmented Generation.

Why this exists: an LLM only knows what it was trained on. It has NOT read your
company's exchange circulars, so if you ask about them it will guess (a
"hallucination"). RAG fixes that:
  1. store your documents,
  2. find the chunks most relevant to a question,
  3. hand those chunks to the LLM so it answers from REAL text and cites sources.

This version runs 100% locally and FREE:
  - the embedding model runs ON YOUR MACHINE (no API, no cost),
  - which also means sensitive documents never leave your computer -
    a real selling point in a regulated industry.
"""
import os
from pathlib import Path
import numpy as np

# The model is already downloaded and cached locally (~80MB, one-time). Without
# this, sentence_transformers/huggingface_hub still does a HEAD request to
# huggingface.co on EVERY import (server startup) just to check for updates —
# if that network call is slow/blocked (corporate firewall, flaky connection,
# huggingface.co having issues), it retries 5 times with growing backoff
# (1s, 2s, 4s, 8s, 16s ≈ 30+ seconds) and can look like the server has hung.
# Setting these BEFORE importing sentence_transformers skips that network call
# entirely and loads straight from the local cache — instant, no internet
# needed. Override in .env (HF_HUB_OFFLINE=false) only if you ever need to
# force a re-check/re-download (e.g. first-ever run on a brand new machine
# that doesn't have the model cached yet).
os.environ.setdefault("HF_HUB_OFFLINE", os.getenv("HF_HUB_OFFLINE", "1"))
os.environ.setdefault("TRANSFORMERS_OFFLINE", os.getenv("HF_HUB_OFFLINE", "1"))

from sentence_transformers import SentenceTransformer

# Folder holding your .txt documents (one file = one source).
DOCS_DIR = Path(__file__).resolve().parent.parent / "data" / "circulars"

# A small, fast, free embedding model. Downloads ONCE (~80MB) then runs locally.
_model = SentenceTransformer("all-MiniLM-L6-v2")


def _load_chunks():
    """Read every .txt file and split it into paragraph-sized chunks.
    Returns a list of (source_filename, chunk_text)."""
    chunks = []
    for path in sorted(DOCS_DIR.glob("*.txt")):
        text = path.read_text(encoding="utf-8")
        # Split on blank lines -> paragraphs. Each paragraph is one "chunk".
        # (Chunking strategy matters in real systems; paragraphs are a fine start.)
        for para in text.split("\n\n"):
            para = para.strip()
            if para:
                chunks.append((path.stem, para))
    return chunks


# Load + embed everything ONCE when this module is first imported.
_chunks = _load_chunks()
_chunk_texts = [c[1] for c in _chunks]
_chunk_vectors = _model.encode(_chunk_texts) if _chunk_texts else np.array([])


def retrieve(question: str, top_k: int = 3):
    """Return the top_k chunks most relevant to the question,
    as a list of (source, text, score)."""
    if len(_chunk_texts) == 0:
        return []
    q_vec = _model.encode([question])[0]
    # Cosine similarity: how closely two vectors point in the same direction.
    sims = _chunk_vectors @ q_vec / (
        np.linalg.norm(_chunk_vectors, axis=1) * np.linalg.norm(q_vec) + 1e-9
    )
    top_idx = np.argsort(sims)[::-1][:top_k]
    return [(_chunks[i][0], _chunks[i][1], float(sims[i])) for i in top_idx]