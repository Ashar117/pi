# Track 1 (MemoryAgent) — Demo Script

**Target:** ~3-minute video — a terminal window and the memory dashboard
(`/memory`) side by side, so retrieval scores and the forgetting ledger are
*seen*, not just narrated.
**Where:** the Alibaba ECS box (`deploy/alibaba/README.md`), so the deployment is visibly real.
**Judging criteria this maps to:** efficient storage/retrieval · timely forgetting ·
recalling critical memories within limited context windows.

## Setup (before recording)

```bash
export PI_SHOW_COST=on          # every turn prints provider/model — judges SEE qwen/qwen3.7-max
export QWEN_API_KEY=sk-...      # Qwen is first in every tier; all calls route to DashScope
python pi_daemon.py             # brain server + agent, so /memory is live alongside the CLI
```

Open two windows: a terminal running `python pi_agent.py` (or Telegram/web chat — the
CLI is simplest to narrate over) for the conversation, and a browser tab at
`http://<host>:7712/memory` (paste the `PI_SERVER_TOKEN` into the token field once) for
the dashboard. Keep both in frame for beats 2–3.

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
`qwen/qwen3.7-max`), L2 distillation, and `[Memory] L3 embedding backfill: N row(s)`
— the facts just became dense-searchable vectors via DashScope `text-embedding-v4`.

**Cut to the dashboard** (`/memory`, left panel — "Memory state"): the three facts just
taught now appear as hot L3 rows, importance-ranked. This is the first visual proof —
memory isn't a claim, it's a row on screen.

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

**Cut to the dashboard's "Live retrieval" panel:** type the exact same paraphrase into
the query box there. The hit list shows the zebrafish fact with its fused score and a
visual bar — this is the number the mechanism above was talking about, on screen, not
asserted. If you have time, also type a lexical near-miss query to show a *lower* score,
underscoring that the ranking is real, not decorative.

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

**The money shot — the dashboard's "Forgetting ledger" panel** shows this live (it
auto-refreshes every 10s; give it a moment after the write). Same data is available on
the CLI if you'd rather narrate over a terminal:

```bash
python scripts/memory_cli.py forgotten --days 7
```

Narrate while it appears: "every forgetting mechanism — expiry, contradiction,
dedup-merge — leaves a trace. This is the ledger: what Pi forgot, when, and why."
Point at each reason as it appears (the dashboard color-codes these — amber/red/blue):
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
- the cost footer: every call `qwen/qwen3.7-max` via DashScope (Alibaba Cloud)
- `curl http://<ecs-ip>:7712/health` from a second terminal — backend live on ECS
- the repo: `core/providers/qwen.py` = Alibaba proof-of-use file

## Rehearsal checklist

- [ ] `QWEN_API_KEY` live smoke passed (one chat turn + one embedding) *before* recording
- [ ] Beat 1 facts actually land in L3 (check `python scripts/memory_cli.py list`)
- [ ] Backfill line appears at exit (needs ≥1 new L3 row this session)
- [ ] Beat 2 paraphrase works on the ECS box, not just locally
- [ ] `/memory` dashboard reachable on the ECS box (`PI_SERVER_HOST=0.0.0.0` + `PI_SERVER_TOKEN` set — see deploy/alibaba/README.md) and the token pasted into the page before recording
- [ ] Dashboard's three panels populate against real data, not an empty agent
- [ ] Expired-fact seed planted the day before for Beat 3
- [ ] Terminal font large enough for a phone-sized video player; browser zoomed in enough that dashboard table text reads on a phone screen too
