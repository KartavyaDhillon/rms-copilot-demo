"""
Throwaway test: proves your LLM connection works.
Run it with:  python ask.py
You can delete this file afterwards.
"""
from core.llm import ask_llm

reply = ask_llm("In one simple sentence, what is 'peak margin' in stock broking?")
print("\nLLM says:\n", reply, "\n")