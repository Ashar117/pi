# Track 1 (MemoryAgent) — Demo Script

**Target:** ~3-minute video, one continuous terminal (plus one Supabase/SQLite peek).
**Where:** the Alibaba ECS box (`deploy/alibaba/README.md`), so the deployment is visibly real.
**Judging criteria this maps to:** efficient storage/retrieval · timely forgetting ·
recalling critical memories within limited context windows.

## Setup (before recording)

```bash
export PI_SHOW_COST=on          # every turn prints provider/model — judges SEE qwen/qwen-max
export QWEN_API_KEY=sk-...      # Qwen is first in every tier; all calls route to DashScope
python pi_agent.py
```

Optional narration line while it boots: "Pi's memory is three-tiered — a raw log,
distilled durable facts, and a token-budgeted hot cache injected every turn.
Qwen on Alibaba Cloud does the thinking, the summarizing, AND the embedding."

## Beat 1 — Teach (≈45s)

Say naturally (don't dictate "store this"):

```
we switched the lab's model organism to zebrafish last week
```
```
i prefer my briefings under 100 words, bullet points only
```
```
remind me the qwen hackathon deadline is july 21
```

Then exit:

```
exit
```

**Point at the exit output:** session summary written by *Qwen* (cost footer shows
`qwen/qwen-max`), L2 distillation, and `[Memory] L3 embedding backfill: N row(s)`
— the facts just became dense-searchable vectors via DashScope `text-embedding-v3`.

## Beat 2 — Cross-session paraphrase recall (≈60s, the money shot)

**Fresh process** (narrate: "new process, empty context window — everything it
knows now comes from memory, not the chat"):

```bash
python pi_agent.py
```

Ask a **paraphrase** — zero lexical overlap with what was stored:

```
which animal is the experiment on these days?
```

Pi answers zebrafish. Narrate the mechanism: "no keyword matches — 'animal' and
'experiment' appear nowhere in the stored fact. The hybrid retriever embedded my
question, cosine-matched it against the memory vectors, fused that with BM25, and
injected the fact into a *token-budgeted* context block. That's recall within a
limited context window, not context stuffing."

Follow with the preference proof:

```
give me a briefing
```

→ arrives under 100 words, bulleted (the stored preference shaped behavior without
being asked).

## Beat 3 — Timely forgetting (≈30s)

```
remember just for today: the wifi password at this cafe is FISH123
```

**No expiry was specified — Pi infers it.** Point at the response: it should
confirm something like "noted — I'll forget that after today." That confirmation
comes from `auto_expiry` in the write result: a deterministic phrase table
(`just for today`, `until friday`, `for the next 3 days`, …) detects ephemeral
wording and sets `active_until` automatically, no ISO datetime required from the
model (T-299).

Show the row's `active_until` (`python scripts/memory_cli.py why <id-prefix>` or a
SQLite peek), then narrate: "expiring facts carry an `active_until`; the retention
tick prunes them daily — and superseded facts are *invalidated, not deleted*, so
'what did I tell you before' still works. Forgetting is a feature, not data loss."

**The money shot — one command shows the whole forgetting ledger:**

```bash
python scripts/memory_cli.py forgotten --days 7
```

Narrate while it prints: "every forgetting mechanism — expiry, contradiction,
dedup-merge — leaves a trace. This is the ledger: what Pi forgot, when, and why."
Point at each reason as it appears:
- **EXPIRED** — the FISH123 wifi fact (or a pre-seeded expired row) landing here proves
  the lifecycle closes the loop, not just opens it.
- **CONTRADICTED** — a fact you corrected mid-conversation ("actually I moved to a new
  apartment") shows up pointing at nothing (SQLite doesn't store the winner locally —
  Supabase does), which is itself an honest detail worth narrating if asked.
- **MERGED** — if the demo wrote a near-duplicate fact earlier, it shows here with
  `-> merged into: '<winner content>'`.

If a pre-seeded expired row exists (seed one the day before with a 1-hour expiry),
show that asking about it returns nothing, then confirm it via `memory_cli forgotten`.

## Beat 4 — Close (≈20s)

Point at:
- the cost footer: every call `qwen/qwen-max` via DashScope (Alibaba Cloud)
- `curl http://<ecs-ip>:7712/health` from a second terminal — backend live on ECS
- the repo: `core/providers/qwen.py` = Alibaba proof-of-use file

## Rehearsal checklist

- [ ] `QWEN_API_KEY` live smoke passed (one chat turn + one embedding) *before* recording
- [ ] Beat 1 facts actually land in L3 (check `python scripts/memory_cli.py list`)
- [ ] Backfill line appears at exit (needs ≥1 new L3 row this session)
- [ ] Beat 2 paraphrase works on the ECS box, not just locally
- [ ] Expired-fact seed planted the day before for Beat 3
- [ ] Terminal font large enough for a phone-sized video player
