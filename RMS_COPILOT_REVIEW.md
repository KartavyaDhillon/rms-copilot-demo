# RMS Copilot — Grounded Architecture Review & Roadmap

**Reviewed:** 2026-06-20 · against the live repo `D:\CLAUDE\rms-copilot` (git `6a1cebe` + uncommitted working tree).
**Method:** source read directly, **not executed**. "Built" means the code exists and is structured correctly — it does *not* mean execution-tested. The one pipeline inspected closely (70-files) has a real unguarded crash, so read every "Built" as *build-complete, needs a test pass*.
**Confidence tags:** `[Certain]` = read it in the code · `[Likely]` = strong inference · `[Guess]` = gap-filling.

---

## 1. The one-paragraph truth

The roadmap you were handed lists ~9 features to "add." Checked against the actual code, **~7 already exist** — several built well, with explicit anti-hallucination boundaries. The reviewer couldn't see that because they had only your folder names (which is why they ended by asking for a ZIP). Your real bottleneck is **not missing features — it's a trust layer that confabulates on anything outside its known-error list.** In an RMS/compliance setting, a confident wrong answer to a risk question is a penalty/audit event, so reliability is the highest-value work, not feature #7. Exactly one proposed item (the Scenario Simulator) is genuinely new, and it is blocked on a **data source** (position/SPAN data), not on AI.

---

## 2. Grounded capability inventory

| Capability | Roadmap said | Reality | Implemented in |
|---|---|---|---|
| NL "Ask RMS Anything" query | "add" (#1) | **Built — the right way** `[Certain]` | `core/agent.py` `ask_data` + `_QUERY_TRANSLATE_SYSTEM` (l.767+) |
| Explainability / audit trail | "4/10, add" (#2) | **Built & disciplined (~7/10)** `[Certain]` | `core/root_cause.py` `analyze_margin_shortage()` |
| Circular Q&A (RAG) | "add" (#3) | **Built** `[Certain]` | `core/rag.py` over `data/circulars/*.txt` |
| Daily RMS health check | "add" (#4) | **Mostly built** `[Certain]` | `core/daily_summary.py` + `core/readiness.py` |
| Alerting | "5/10, add" (#5) | **Partial** `[Certain]` | `core/notify.py` (WhatsApp + email) |
| Root-cause agent | "add, valuable" (#6) | **Built** `[Certain]` | `core/root_cause.py` |
| Code / knowledge search | "halfway" | **Built** `[Certain]` | `core/code_index.py` `search_code` |
| Known-error instant answers | not listed | **Built — your best pattern** `[Certain]` | `core/agent.py` `_match_known_error()` |
| Dashboard | "6/10" | **Built** `[Certain]` | `dashboard.html` (97 KB) |
| RMS pipelines (15 modules) | "9/10" | **Built; reliability unproven** `[Certain exist / Guess on score]` | `core/pipelines/*` |

**Why the two highest-value ones are better than advertised:**

- **NL query (`ask_data`).** The LLM only translates English → a strict JSON spec (`filter` ops `gt/lt/gte/lte/contains/equals`, `action` `count/sum/average/max/min/top/bottom/lookup`, `group_by`, `n`); **pandas executes it.** So *"clients with shortage above ₹50,000"* becomes `{"filter":[{"op":"gt","value":50000,...}],"action":"list"}` and the number never comes from the model. This is exactly the architecture you want, and it already exists.
- **Explainability / root cause (`root_cause.py`).** Decomposes `shortfall = deposit NMASS − total margin` and `margin warning % = derivative margin ÷ deposit × 100`, and **explicitly refuses** to invent a trade-level cause the file doesn't contain. That refusal boundary is the gold standard; the reviewer's "4/10" is wrong because they couldn't see this file.

**Genuinely missing:** Scenario simulator (needs position/SPAN data) · Microsoft Teams channel · failure / missing-file alerting.

---

## 3. Architecture — how it's actually wired

- **API:** FastAPI + Server-Sent Events streaming (`api.py`) — `/agent`, `/ask`, and per-pipeline `*-stream` endpoints. Front end is the single `dashboard.html`.
- **LLM:** **Groq free tier, `llama-3.3-70b-versatile`** (`core/llm.py`; `core/config.py` comments: *"free… used now / Anthropic later when funded"*). One swap-point (`ask_llm`) — clean dependency inversion.
- **Agent:** `core/agent.py` (~104 KB) — 20+ tools, deterministic keyword process-routing, token-diet prompt assembly, the zero-AI known-error fast path, and a JSONL audit log (`logs/agent_log.jsonl`).
- **Data:** reads real pipeline outputs from `D:\…` backup folders; RAG over `data/circulars`.
- **Secrets:** `.env` (Groq, SMTP/Netcore, FTP, WhatsApp).

### Strengths — keep these
1. **Module-level anti-hallucination discipline.** `root_cause`, `daily_summary`, `rag`, `ask_data` all "compute from files, refuse to invent." Rare and genuinely good.
2. **LLM-translates / pandas-computes split.** Numbers are never model output.
3. **Zero-AI known-error path.** Instant, free, cannot hallucinate — and your logs show it returning correct answers.
4. **Single LLM seam (`llm.py`).** Provider swap is one file.
5. **Human-in-the-loop sends.** `notify.py` fires only "after approval OR `AUTO_SEND=true`."

### Risks — fix these
1. **The discipline stops at the orchestrator.** Modules refuse to invent; the agent's *error / unknown* path does not — that is the confabulation you saw. The captured traceback (`files_downloader_70_run.py` → `holder["trace"] = traceback.format_exc()`) is **discarded before the agent sees it** — the agent's only mentions of `trace` are the literal word `"traceback"` in a keyword list (l.1722) and the `run_agent_traced` function name (l.1865); it never reads the error event's `trace` field. The one datum that names the failing line is thrown away.
2. **A free-tier model is the brain.** ~6,000 TPM, rate-limit fallbacks already in code, and a 70B open model follows a long, rule-dense SYSTEM prompt less reliably than the prompt assumes — directly relevant to the confabulation. Fine for dev; a known ceiling for production trust.
3. **104 KB monolith.** Routing + 20 tools + KB + prompt + logging in one file; reliability bugs hide in the seams.
4. **No visible test suite; single git commit.** One unit test on the login-page parse would have caught the 70-files crash. You cannot safely refactor the monolith without tests.
5. **Config drift.** `config.py` names Gupshup for WhatsApp; `notify.py` calls Tata-Tele omni endpoints. Pick one, delete the other.
6. **Plaintext secrets in `.env`.** Acceptable for a single internal box — just a noted assumption, not a free pass.

---

## 4. Priority zero — the trust layer (do this before any feature)

The earlier failure (`🔥 TypeError: 'NoneType' object is not subscriptable`) is the canonical case. Four moves, in leverage order:

1. **Pipe the captured `trace` into agent context** on error questions, and render it collapsed in the UI. ~3 lines; highest payoff. Without the line number nobody can diagnose; with it, even a knowledge-base miss becomes solvable.
2. **Add the NoneType / login-scrape error to the KB** so this whole class gets the instant grounded answer instead of the LLM. The likely root cause: `soup.find(id="__VIEWSTATE")["value"]` (`files_downloader_70.py:348–352`, again 388–410) — when the exchange returns an expired-session / captcha / changed page, `.find()` is `None` and `None["value"]` throws exactly this error.
3. **Guard the scrape at the source:** a `must_find(soup, id=…)` helper that raises `"NSE login page missing __VIEWSTATE — expired session / captcha / markup change"` instead of letting `None["value"]` surface a generic TypeError. The message becomes the diagnosis.
4. **Add one SYSTEM rule:** *"If you do not have the traceback in context, do not propose a cause — say you need it and ask the user to paste it, or read the run log. State a cause only if you can quote the line or log you read."* Pair the existing "never invent" prohibition with a permitted action.

**Principle:** extend the module discipline to the orchestrator — *the agent may narrate over tool output; it is never a source of facts or causes.*

---

## 5. Roadmap (phased)

**Phase 0 — Trust (1–2 days, now).** The four fixes above + one unit test on the login parse. Outcome: novel errors get a grounded answer or an honest "paste the traceback," never fiction.

**Phase 1 — Consolidate what exists into one surface (~1 week).** Fold `daily_summary` + `readiness` + FTP/NSE file status into a single morning health view (you already have the parts). Surface `root_cause` in chat ("why is X short"). Add failure / missing-file alerts to `notify` (the send layer already exists). Add the Teams channel.

**Phase 2 — Deepen explainability (~1 week).** Turn `root_cause` into an exportable audit trail (component breakdown + source file + timestamp) that RMS/audit can retain. Keep the "no trade-level cause" boundary.

**Phase 3 — Scenario simulator (scoped, gated).** Step one is **data, not AI**: can you obtain position/SPAN-level files? If yes, build a margin-recompute that shocks prices and re-runs the shortfall math. If no, do not start — `root_cause.py`'s own boundary note confirms the current output files lack position data.

**Cross-cutting (ongoing).** A pytest suite over the parse/format seams; decompose `agent.py` (split tools / routing / KB / logging); grow `logs/agent_log.jsonl` into a tiny regression eval (you already log every Q&A — promote the good answers to checks); pre-decide the funded-LLM trigger (the Anthropic swap is one file in `llm.py` when trust matters enough).

---

## 6. What NOT to build (endorsed)

The reviewer's "don't" list is correct — make it written policy: **no** trade recommendations, **no** automated risk overrides (release holdings / change limits / approve payouts / modify settings), **no** direct exchange / penalty / regulatory uploads without human approval. Advisory + human-in-the-loop only. You already enforce the spirit (`notify` gates on approval); write it down so it survives future features.

---

## 7. This week

1. Trace plumbing into the agent + UI (Phase 0.1).
2. Add the NoneType / login error to the KB (Phase 0.2).
3. `must_find` guard in the 70-files login parse + one test (Phase 0.3 + cross-cutting).
4. One SYSTEM rule for the no-eviden