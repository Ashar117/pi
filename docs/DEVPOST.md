# Devpost Submission — paste-ready draft

**Track: 1 — MemoryAgent**

## Project name

**Pi — a personal agent whose memory survives the conversation**

## Elevator pitch (Devpost tagline, ≤200 chars)

A personal AI agent with three-tier persistent memory: Qwen thinks, summarizes and
embeds; Pi decides what to keep, what to recall into a limited context window, and
what to forget.

## What it does

Pi is a personal AI agent built around one bet: **memory is the product**. Most
"memory" features are context stuffing — Pi instead runs a three-tier memory system
with real write/read/forget lifecycle:

- **L1 — raw log**: every conversation turn, archived (pruned at ~30 days).
- **L2 — durable facts**: at session end, **Qwen distills** the conversation into
  discrete facts with importance scores and categories. Semantic dedup (cosine over
  **Qwen `text-embedding-v4`** vectors) stops the same fact accumulating as paraphrases.
- **L3 — hot cache**: the facts that matter now, injected into the system prompt every
  turn under a **hard token budget** — recall within a *limited* context window is the
  design constraint, not an accident.

**Retrieval is hybrid and query-aware.** Every turn, the user's message is embedded
(Qwen) and fused — dense cosine + BM25 + importance/recency/context boosts — across
L3+L2. The proof case in our test suite: store *"the lab uses zebrafish as the model
organism"*, then a fresh process asks *"which animal is the experiment on now?"* —
zero lexical overlap, BM25 alone finds nothing, the hybrid retriever surfaces it.

**Forgetting is first-class.** Facts can carry an expiry (`active_until`) pruned by
retention policies; contradicted facts are *invalidated, not deleted* — "what did I
tell you before?" still answers honestly. Ebbinghaus-style decay + pinning tune what
stays hot.

**It improves with use.** Cross-session accuracy compounds: preferences shape behavior
without being re-stated, project facts recall across restarts, and mode/conversation
context boosts retrieval of memories encoded in similar contexts.

## How Qwen Cloud powers it

- **qwen3.7-max** (DashScope OpenAI-compatible API) is first in every routing tier — it
  runs the conversation, the session summaries, and the L2 fact distillation.
  *Qwen writes the memories, Qwen recalls them.*
- **text-embedding-v4** embeds every memory and every query for the dense half of the
  hybrid retriever and semantic dedup.
- The router enforces a **per-provider daily token budget** with automatic brownout +
  failover — production cost discipline, not a demo loop.
- Backend deployed on **Alibaba Cloud ECS**.

**Alibaba Cloud proof-of-use:** [`core/providers/qwen.py`](../core/providers/qwen.py)
(chat) and [`memory/semantic_dedup.py`](../memory/semantic_dedup.py) (embeddings) —
both hit the DashScope endpoint.

## How we built it

Python, stdlib-first. FastAPI brain server (SSE streaming, Bearer auth), SQLite for
the hot tier, Supabase for durable tiers, `rank-bm25` + in-process cosine for
retrieval (no vector DB — measured scale doesn't need one). Every change ships behind
a CI gate (`scripts/verify.py`): 310 files, 160+ test files, a keystone
"conversation coherence" gate, and **memory round-trip contract tests** that assert
write→read is content-preserving for every tier — because the #1 recurring bug class
in memory systems is write/read divergence.

## Challenges

1. **Paraphrase recall**: lexical search silently fails the moment the user rephrases.
   Fixing it required embeddings on the *hot* tier and score fusion — without slowing
   the interactive write path (embeddings backfill at session exit instead).
2. **Honest forgetting**: deleting contradicted facts breaks "what did I say before";
   keeping them poisons recall. Invalidation (soft, queryable) solved both.
3. **Degraded-data honesty**: cached snapshots must decline questions they can't
   actually answer rather than serve adjacent data (our "I didn't ask for bitcoin" bug).

## Accomplishments

- A reproducing test that *proves* the paraphrase gap and its fix, not just claims it
- 320+ closed engineering tickets with an append-only solutions log — the agent's own
  development runs a build→test→ticket→verify loop
- Memory ops (summarize/distill/embed) fully on Qwen with budget-aware routing

## What's next

Scheduled consolidation ("sleep"), retrieval-quality eval harness, and letting Pi's
autonomous sprint runner close its own memory-improvement tickets.

## Links

- **Repo (public, Apache-2.0):** https://github.com/Ashar117/pi
- **Alibaba Cloud proof:** https://github.com/Ashar117/pi/blob/main/core/providers/qwen.py
- **Video:** _[YouTube link — record per docs/DEMO_SCRIPT.md]_
- **Architecture diagram:** rendered in the repo README (mermaid)

---

*Fill before submitting: video URL, ECS deployment note/screenshot, team info.*
