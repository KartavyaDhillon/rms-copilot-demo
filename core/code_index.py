"""
core/code_index.py — Code Understanding for the agent.
========================================================
Indexes every .py file in CODE_FOLDERS so the agent can answer:
  "which script creates OFS.csv?"  → file + line number + snippet
  "where is the captcha handled?"  → semantic match
  "show me the TOP20 password logic"

Two search passes, deterministic first:
  1. EXACT pass  — if the query names a file/string (OFS.csv, "Update Limits",
                   run_ctcl), grep every chunk for it. Cannot miss, cannot hallucinate.
  2. SEMANTIC pass — sentence-transformers embeddings (same local model RAG uses,
                   already cached on this machine) for concept questions.

Embeddings are cached to disk and rebuilt only when source files change.
No new dependencies, no external services, fully offline.
"""
import os
import re
import json
import hashlib

# Folders to index: comma-separated in .env. Defaults to the project's core/
# plus the project root. ADD YOUR ORIGINAL SCRIPTS FOLDER in .env, e.g.:
#   CODE_FOLDERS=D:\CLAUDE\rms-copilot\core,D:\RMS_ORIGINAL_SCRIPTS
_DEFAULT_FOLDERS = os.pathsep.join([
    os.path.join(os.getcwd(), "core"),
    os.getcwd(),
])
CODE_FOLDERS = [f.strip() for f in
                os.getenv("CODE_FOLDERS", _DEFAULT_FOLDERS).replace(",", os.pathsep).split(os.pathsep)
                if f.strip()]

_CACHE_DIR = os.path.join(os.getcwd(), ".code_index")
CHUNK_LINES = 40          # lines per chunk
CHUNK_OVERLAP = 8         # lines of overlap between chunks
MAX_FILE_KB = 512         # skip giant files

_chunks = None            # [{file, start, end, text}]
_embeddings = None        # numpy array aligned with _chunks
_model = None


def _iter_py_files():
    seen = set()
    for folder in CODE_FOLDERS:
        if not os.path.isdir(folder):
            continue
        for root, dirs, files in os.walk(folder):
            # skip caches, venvs, the index itself
            dirs[:] = [d for d in dirs if d not in
                       (".venv", "venv", "__pycache__", ".git", ".code_index",
                        "node_modules", "BACKUPFILES AUTOMATED", "BACKUP AUTOMATION")]
            for f in files:
                if f.endswith(".py"):
                    fp = os.path.abspath(os.path.join(root, f))
                    if fp not in seen and os.path.getsize(fp) <= MAX_FILE_KB * 1024:
                        seen.add(fp)
                        yield fp


def _source_fingerprint():
    h = hashlib.md5()
    for fp in sorted(_iter_py_files()):
        try:
            h.update(fp.encode())
            h.update(str(os.path.getmtime(fp)).encode())
        except OSError:
            pass
    return h.hexdigest()


def _build_chunks():
    chunks = []
    for fp in _iter_py_files():
        try:
            lines = open(fp, encoding="utf-8", errors="replace").read().splitlines()
        except OSError:
            continue
        step = CHUNK_LINES - CHUNK_OVERLAP
        for start in range(0, max(len(lines), 1), step):
            seg = lines[start:start + CHUNK_LINES]
            if not seg:
                continue
            text = "\n".join(seg)
            if text.strip():
                chunks.append({
                    "file": fp,
                    "start": start + 1,
                    "end": start + len(seg),
                    "text": text,
                })
    return chunks


def _ensure_index():
    """Load chunks (and embeddings if cached & fresh). Embeddings built lazily."""
    global _chunks, _embeddings
    if _chunks is not None:
        return
    os.makedirs(_CACHE_DIR, exist_ok=True)
    fp_meta = os.path.join(_CACHE_DIR, "meta.json")
    fp_chunks = os.path.join(_CACHE_DIR, "chunks.json")
    fp_emb = os.path.join(_CACHE_DIR, "embeddings.npy")
    fingerprint = _source_fingerprint()

    if os.path.exists(fp_meta) and os.path.exists(fp_chunks):
        try:
            meta = json.load(open(fp_meta))
            if meta.get("fingerprint") == fingerprint:
                _chunks = json.load(open(fp_chunks))
                if os.path.exists(fp_emb):
                    try:
                        import numpy as np
                        _embeddings = np.load(fp_emb)
                        if len(_embeddings) != len(_chunks):
                            _embeddings = None
                    except Exception:
                        _embeddings = None
                return
        except Exception:
            pass

    _chunks = _build_chunks()
    _embeddings = None  # built on first semantic search
    json.dump(_chunks, open(fp_chunks, "w"))
    json.dump({"fingerprint": fingerprint, "files": len({c['file'] for c in _chunks}),
               "chunks": len(_chunks)}, open(fp_meta, "w"))
    # stale embeddings no longer match
    if os.path.exists(fp_emb):
        try:
            os.remove(fp_emb)
        except OSError:
            pass


def _ensure_embeddings():
    """Build/load embeddings lazily. Uses the same local model as RAG (cached)."""
    global _embeddings, _model
    if _embeddings is not None:
        return True
    try:
        import numpy as np
        # Model is already cached locally — force offline so this never tries a
        # network call to huggingface.co (same fix as core/rag.py; see that
        # file's comment for why this matters). setdefault so an explicit
        # HF_HUB_OFFLINE=false in .env still wins if someone ever needs it.
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        from sentence_transformers import SentenceTransformer
        if _model is None:
            _model = SentenceTransformer("all-MiniLM-L6-v2")
        texts = [f"{os.path.basename(c['file'])}\n{c['text'][:600]}" for c in _chunks]
        _embeddings = _model.encode(texts, show_progress_bar=False, batch_size=64)
        np.save(os.path.join(_CACHE_DIR, "embeddings.npy"), _embeddings)
        return True
    except Exception:
        return False  # exact search still works


def _exact_terms(query: str):
    """Pull literal things worth grepping: filenames, quoted strings, identifiers."""
    terms = []
    terms += re.findall(r'"([^"]{3,})"', query)
    terms += re.findall(r"'([^']{3,})'", query)
    terms += re.findall(r"\b[\w\-]+\.(?:csv|xlsx|xls|txt|json|py|bat|png|lis)\b",
                        query, flags=re.I)
    terms += re.findall(r"\b(?:run_|process_|stream_|download_)[a-z0-9_]+\b", query, flags=re.I)
    # also significant content words (so plain-English queries get a grep pass,
    # not only semantic) — skip short words and common filler.
    _STOP = {"which","script","scripts","where","what","file","files","function",
             "code","the","show","tell","find","does","creates","writes","write",
             "create","read","reads","this","that","from","with","have","there",
             "about","please","into","output","process"}
    for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]{4,}", query):
        if w.lower() not in _STOP:
            terms.append(w)
    # dedupe, keep order
    out, seen = [], set()
    for t in terms:
        tl = t.lower()
        if tl not in seen:
            seen.add(tl)
            out.append(t)
    return out


def search_code(query: str, k: int = 3) -> dict:
    """
    Search the indexed source code. Returns exact (grep) hits first, then
    semantic hits. Every hit carries file, line range, and a snippet.
    """
    _ensure_index()
    if not _chunks:
        return {"error": "No .py files indexed. Set CODE_FOLDERS in .env to your "
                         "script folders and restart.",
                "folders_checked": CODE_FOLDERS}

    results, used_lines = [], set()

    # ── pass 1: EXACT (deterministic — cannot miss what exists) ───────────
    terms = _exact_terms(query)
    for term in terms:
        tl = term.lower()
        for c in _chunks:
            if tl in c["text"].lower():
                key = (c["file"], c["start"])
                if key in used_lines:
                    continue
                used_lines.add(key)
                # find the precise first matching line
                line_no = c["start"]
                for i, ln in enumerate(c["text"].splitlines()):
                    if tl in ln.lower():
                        line_no = c["start"] + i
                        break
                results.append({
                    "match_type": "exact",
                    "matched": term,
                    "file": c["file"],
                    "line": line_no,
                    "snippet": c["text"][:400],
                })
                if len(results) >= k * 2:
                    break
        if len(results) >= k * 2:
            break

    # ── pass 2: SEMANTIC (concept questions) ──────────────────────────────
    semantic_note = None
    if len(results) < k:
        if _ensure_embeddings():
            try:
                import numpy as np
                qv = _model.encode([query])[0]
                emb = _embeddings
                sims = emb @ qv / ((np.linalg.norm(emb, axis=1) * np.linalg.norm(qv)) + 1e-9)
                order = np.argsort(-sims)
                for idx in order[: k * 3]:
                    c = _chunks[int(idx)]
                    key = (c["file"], c["start"])
                    if key in used_lines:
                        continue
                    used_lines.add(key)
                    results.append({
                        "match_type": "semantic",
                        "score": round(float(sims[int(idx)]), 3),
                        "file": c["file"],
                        "lines": f"{c['start']}-{c['end']}",
                        "snippet": c["text"][:400],
                    })
                    if len(results) >= k + len(terms):
                        break
            except Exception as e:
                semantic_note = f"semantic search failed: {e}"
        else:
            semantic_note = ("semantic model unavailable — exact results only "
                             "(model loads automatically on the main machine)")

    out = {"query": query,
           "indexed_files": len({c['file'] for c in _chunks}),
           "results": results[: max(k, 4)]}
    if semantic_note:
        out["note"] = semantic_note
    if not results:
        out["note"] = (f"No matches for '{query}' across "
                       f"{len({c['file'] for c in _chunks})} indexed files. "
                       f"Folders: {CODE_FOLDERS}")
    return out


def rebuild_index() -> dict:
    """Force a rebuild (e.g. after adding scripts)."""
    global _chunks, _embeddings
    _chunks = None
    _embeddings = None
    for f in ("meta.json", "chunks.json", "embeddings.npy"):
        fp = os.path.join(_CACHE_DIR, f)
        if os.path.exists(fp):
            try:
                os.remove(fp)
            except OSError:
                pass
    _ensure_index()
    return {"status": "rebuilt",
            "files": len({c['file'] for c in _chunks}),
            "chunks": len(_chunks),
            "folders": CODE_FOLDERS}


def list_indexed_code() -> dict:
    """
    Show exactly which script files are indexed and how much of each.
    Lets you VERIFY the agent is reading every script — and spot folders
    that aren't pointed at yet. No LLM, instant.
    """
    _ensure_index()
    if not _chunks:
        return {"error": "Nothing indexed. Set CODE_FOLDERS in .env to your script "
                         "folders and restart.",
                "folders_configured": CODE_FOLDERS}
    by_file = {}
    for c in _chunks:
        by_file.setdefault(c["file"], {"chunks": 0, "lines": 0})
        by_file[c["file"]]["chunks"] += 1
        by_file[c["file"]]["lines"] = max(by_file[c["file"]]["lines"], c["end"])
    files = [{"file": os.path.basename(f),
              "path": f,
              "lines": v["lines"],
              "chunks": v["chunks"]}
             for f, v in by_file.items()]
    files.sort(key=lambda x: x["file"].lower())
    return {
        "folders_configured": CODE_FOLDERS,
        "total_files": len(files),
        "total_chunks": len(_chunks),
        "files": files,
    }
