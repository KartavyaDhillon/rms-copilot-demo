"""
LLM client — the ONE place in the whole project where we talk to a language model.

Today this uses Groq (free tier, no credit card). If you ever switch to
Anthropic's Claude or anything else, you change ONLY this file — the rest of
the project just calls ask_llm() and never needs to know who the provider is.
That is the "Dependency Inversion Principle": depend on an abstraction
(ask_llm), not on a concrete vendor.
"""
from groq import Groq
from core.config import GROQ_API_KEY

# Create the client once and reuse it everywhere (cheaper than re-creating it).
_client = Groq(api_key=GROQ_API_KEY)

# Which model to use. This open model is available on Groq's free tier.
# If this exact name ever errors, run `python -c "from groq import Groq; print([m.id for m in Groq().models.list().data])"`
# to see the current model list, and update this string.
MODEL = "llama-3.3-70b-versatile"


def ask_llm(prompt: str, system: str = "You are a helpful assistant.") -> str:
    """
    Send a prompt to the language model and return its text answer.

    prompt : the question / instruction from the user
    system : the 'role' we give the model (sets its behaviour/persona)
    """
    response = _client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    )
    # The API can return several "choices"; we take the first one's text.
    return response.choices[0].message.content