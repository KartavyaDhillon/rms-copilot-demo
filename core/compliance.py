"""
Compliance Assistant logic, as a reusable FUNCTION (not a run-once script).

A script runs top-to-bottom one time. A function can be CALLED again and again -
by a test, by the backend, by an agent. Turning logic into functions is THE
refactor that makes code reusable, which is exactly what a backend needs.
"""
from core.rag import retrieve
from core.llm import ask_llm

SYSTEM = (
    "You are a compliance assistant. Answer the question using ONLY the context "
    "provided. If the answer is not in the context, say you don't know. "
    "Always cite the source name in brackets."
)


def answer_compliance(question: str, top_k: int = 3) -> dict:
    """Return a grounded answer plus the sources used.

    Returns a dict like:
      {"answer": "...", "sources": [{"source": "peak_margin_penalty", "score": 0.69}]}
    """
    hits = retrieve(question, top_k=top_k)
    context = "\n\n".join(f"[Source: {src}]\n{text}" for src, text, _ in hits)
    prompt = f"Context:\n{context}\n\nQuestion: {question}"
    reply = ask_llm(prompt, system=SYSTEM)
    sources = [{"source": src, "score": round(score, 2)} for src, _, score in hits]
    return {"answer": reply, "sources": sources}
