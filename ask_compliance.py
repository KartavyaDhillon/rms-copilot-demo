"""
The Compliance Assistant - your first RAG application.

Ask a question about your circulars; it answers ONLY from the real documents
and tells you which source it used.

Run:  python ask_compliance.py
"""
from core.rag import retrieve
from core.llm import ask_llm

# Try changing this question and re-running!
QUESTION = "What is the penalty if a client's peak margin is short?"

# 1) RETRIEVE the most relevant chunks from your documents
hits = retrieve(QUESTION, top_k=3)

# 2) AUGMENT: build a prompt that includes that real context
context = "\n\n".join(f"[Source: {src}]\n{text}" for src, text, score in hits)

system = (
    "You are a compliance assistant. Answer the question using ONLY the context "
    "provided. If the answer is not in the context, say you don't know. "
    "Always cite the source name in brackets."
)
prompt = f"Context:\n{context}\n\nQuestion: {QUESTION}"

# 3) GENERATE the grounded answer
answer = ask_llm(prompt, system=system)

print("\nQUESTION:", QUESTION)
print("\nANSWER:\n", answer)
print("\n--- sources retrieved ---")
for src, text, score in hits:
    print(f"  {src}  (relevance {score:.2f})")