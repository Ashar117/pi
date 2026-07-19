# Teaching My Agent to Remember: Porting Pi to Qwen Cloud

*Draft for the Qwen Cloud hackathon blog prize. Publish on dev.to / Medium / personal
site; add screenshots from the demo run before publishing.*

---

I've spent months building Pi, a personal AI agent whose entire bet is that **memory
is the product**. Not "we append your chat history" memory — a three-tier system with
a real lifecycle: a raw log (L1), durable facts distilled at session end (L2), and a
token-budgeted hot cache injected into every prompt (L3). When the Global AI Hackathon
with Qwen Cloud announced a MemoryAgent track, the fit was almost suspicious.

This is the story of the port, and of the retrieval bug I found in my own system
along the way.

## The port: one file, honestly

Pi routes every LLM call through a small router — tiers ("premium", "cheap", "fast"),
per-provider daily token budgets, automatic brownout when a provider degrades. Adding
Qwen took one provider file, because DashScope speaks the OpenAI wire format:

```python
_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"

class QwenProvider:
    name = "qwen"
    def __init__(self, api_key, model="qwen3.7-max"):
        from openai import OpenAI
        self._client = OpenAI(api_key=api_key, base_url=_BASE_URL, timeout=30.0)
```

Then `"qwen"` goes first in each routing tier. When `QWEN_API_KEY` is set, Qwen runs
the conversation, the session summaries, and the fact distillation. When it's not,
nothing changes locally. The whole diff was ~40 lines plus tests. Multi-provider
architecture pays for itself the day a hackathon hands you a new provider.

The part I didn't expect to be this smooth: **embeddings**. Pi used Gemini embeddings
for semantic dedup. DashScope's `text-embedding-v4` slots into the same
OpenAI-compatible client, so the embedding function became "try Qwen, fall back to
Gemini" — and suddenly the *entire* memory stack (think, summarize, embed) runs on
Alibaba Cloud.

## The bug the hackathon made me face

Preparing the demo, I wrote this test:

```python
mt.memory_write(content="the lab uses ZEBRAFISH as the model organism", tier="l3")
hits = search("which animal is the experiment on now?")
```

It failed. Of course it failed — my hot tier's search was BM25. *Zero* lexical overlap
between the question and the fact. My agent could remember things and still fail to
recall them the moment you rephrased. For a memory agent, that's not a bug, that's an
identity crisis.

The fix had three parts, each small:

1. **Embed the hot tier.** L3 rows get an embedding column — but written *after* the
   turn (backfilled at session exit), because an interactive write path must never
   block on a network call.
2. **Fuse, don't replace.** A new `retrieve(query, k)` embeds the query once and
   scores candidates with `w_dense · cosine + w_lex · BM25 + w_imp · importance`,
   min-max normalized. BM25 stays — dense-only retrieval has its own embarrassing
   failure modes (exact IDs, ticker symbols, names).
3. **Wire it into the turn loop.** The old path extracted *one keyword* from the user
   message. Now every recall-shaped turn runs the full hybrid retrieval and injects
   the top-k into a token-budgeted context block.

The test now passes with a negative control: BM25-alone finds nothing (proving the
gap was real), the hybrid retriever surfaces the zebrafish fact. That negative
assertion matters — it's the difference between "we added RAG" and "we fixed a
measured failure."

## Forgetting is the underrated half

The track brief asks for "timely forgetting of outdated information," and I think
that's the most product-minded line in the whole hackathon. Two mechanisms in Pi:

- **Expiry**: facts can carry `active_until`; retention policies prune them.
  ("The wifi password at this cafe" should not live forever.)
- **Invalidation, not deletion**: when a new fact contradicts an old one, the loser
  is marked invalid but kept — so "what did I tell you before?" answers honestly
  instead of gaslighting the user.

A memory agent that can't forget is a hoarder; one that deletes is a liar.

## What Qwen Cloud got right

- The OpenAI-compatible endpoint is genuinely compatible — chat, tools, and
  embeddings all worked with the `openai` SDK unmodified.
- `qwen3.7-max` handled the unglamorous jobs (summarize this session into 2–3 sentences;
  extract discrete facts as JSON) reliably, which is exactly what a memory pipeline
  needs — those calls run unattended at session exit.
- Free-tier + budget pressure was a feature: my router's per-provider daily token
  budget and brownout logic exist because cost discipline is architecture, not an
  afterthought.

## The takeaway

If you're building an agent with memory: the storage schema is the easy 20%. The hard
80% is the *lifecycle* — what gets written, what gets recalled into a bounded context
window, what dies, and what survives a paraphrase. Test the round-trip (write→read
content preservation, per tier), test the paraphrase, and make forgetting a feature.

Pi is open source (Apache-2.0): https://github.com/Ashar117/pi — the Qwen provider is
[one readable file](https://github.com/Ashar117/pi/blob/main/core/providers/qwen.py).

*Built for the Global AI Hackathon with Qwen Cloud, Track 1: MemoryAgent.*
