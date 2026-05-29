# Phase 8.8 — The Caretaker Phase

> Pi gets a unit of conversation, a memory caretaker, and provider resilience.

**Status:** planning
**Created:** 2026-05-24
**Tickets:** T-121 → T-129 (11 tickets, ~18-22h)
**Predecessor:** Phase 8.7 (Hardening Track) — closed
**ADR:** ADR-007 — memory lifecycle (skeleton in T-125a, finalised in T-125c)

---

## Why this phase exists

Six bugs surfaced in rapid succession after Phase 8.7 closed:

1. **Gemini 429** — vision falls over with no fallback chain
2. **No tagged-message recall** — Telegram replies-to-Pi can't be referenced
3. **Memory never auto-updates** — "user is 19" stays 19 forever, even past their birthday
4. **Typo / colloquial breakage** — "no sauce in subway aghhh" misread because Pi pulled a noisy session summary instead of the canonical order
5. **Message bombardment** — three rapid `Hey`s get three separate `hey` replies; no conversational bubble
6. **No Telegram lifecycle commands** — no `/exit`, no `/clear`

These aren't six bugs. They're **three root causes** wearing six masks:

| Root cause | Symptoms |
|---|---|
| **A. Pi treats every message as atomic** | bombardment, wasted vision tokens, broken references, naive L2 cadence |
| **B. Memory is append-only, never reconciled** | stale age, contradictory facts, noisy lookup pulling wrong record |
| **C. External providers fail without a chain** | Gemini 429, deferred T-115 Groq tool_use_failed, future STT/TTS 429s |

Phase 8.8 solves all three with four new architectural pieces plus two workflow upgrades plus one generalisation.

---

## Architecture

```text
                  ┌──────────────────────┐
   incoming msg ─▶│  BUBBLE COLLECTOR   │── debounce 6s ──▶ atomic bubble
                  └──────┬───────────────┘   (force-flush on /exit, /clear, media)
                         │
                         ▼
              ┌──────────────────────────────────┐
              │  PARALLEL FAN-OUT (after close)  │
              ├─────────────────┬────────────────┤
              │  TAGGED RECALL  │   THINKING     │  ← Groq → Haiku fallback
              │  (T-123)        │   LAYER (T-124)│     always-on for non-commands
              └────────┬────────┴────────┬───────┘
                       │ ┌───────────────┘
                       ▼ ▼
                ┌──────────────────┐
                │  EXISTING RESPOND │  (root / normie / private — unchanged)
                └──────┬───────────┘
                       │
            ┌──────────┴──────────┐
            ▼                     ▼
    provider chain          bubble close → L2
    (vision/STT/TTS)        single distillation per bubble
    T-121 circuit breakers
                                  │
                                  ▼
                        ┌─────────────────┐
                        │   CARETAKER     │  T-125a lite per-bubble + daily;
                        │  (a → b → c)    │  T-125b full session-exit + daily;
                        └─────────────────┘  T-125c contradictions + ADR
```

---

## Decisions (Ash confirmed)

| # | Question | Ash chose | Notes |
|---|---|---|---|
| 1 | Bubble idle timeout | **6s** (was 12s) | Faster correction window; idle resets on each new message |
| 2 | Thinking layer when | **Always on for non-commands** | Plus Haiku fallback when Groq 429s |
| 3 | AFK behaviour | **REMOVED** | Only `/exit` and `/clear` survive |
| 4 | Caretaker cadence | **Per-bubble lite + daily cron** | Interactive flow never blocks |
| 5 | Phase numbering | **8.8** | Stays in Phase 8 versioning |

---

## Revised ticket list + sequence

Sequence is optimised for **highest-leverage wins first**, with workflow discipline upgrades (T-128, T-127) front-loaded so the rest land cleanly.

| Order | ID | Title | Severity | Effort | Why this slot |
|---|---|---|---|---|---|
| 1 | T-121 | Vision provider chain + 429 circuit breaker | P1 | 2-3h | Independent, low risk, immediate win |
| 2 | T-128 | Pre-close gate + effort calibration | P2 | 1.5h | Discipline scaffolding BEFORE shipping 9 more tickets |
| 3 | T-122 | Telegram message bubble collector (6s) | P1 | 4-6h | Foundation for everything that follows |
| 4 | T-127 | Conversation-to-ticket extractor (Skill 15) | P2 | 2h | Cheap, prevents lost context from this point on |
| 5 | T-125a | Caretaker derived-fact auto-recompute | P1 | 2.5h | Dopamine win: age auto-updates |
| 6 | T-123 | Tagged / reply-to message recall | P2 | 2h | Stops wasting vision tokens |
| 7 | T-124 | Thinking layer (lite) — intent + normalise + Haiku fallback | P2 | 3h | Runs in parallel with recall |
| 8 | T-126 | Telegram lifecycle commands (/exit + /clear only) | P3 | 45min | Needs bubble's flush() ready |
| 9 | T-125b | Caretaker dedup (full mode) | P2 | 2h | After 7-day soak of T-125a |
| 10 | T-125c | Caretaker contradictions + ADR-007 finalised | P2 | 2-3h | Capstone |
| 11 | T-129 | Thinking layer (full) — recall-merge + clarifier | P3 | 2h | Only ships after T-124 soaks 7 days |

**Critical path** (must ship to be Phase 8.8 done): items 1-8 (~17h).
**Stretch** (can slip to Phase 8.9 if needed): items 9-11 (~6h).

---

## Concerns I raised + how they're addressed

| Concern | Mitigation in revised plan |
|---|---|
| Latency stack-up (12s + recall + thinking) | Bubble cut to 6s; recall + thinking run **in parallel** after bubble close; combined ≤ max(200ms, 500ms) |
| T-125 was too big | Split into T-125a / T-125b / T-125c — each independently shippable |
| Thinking layer all-or-nothing | T-124 ships lite (intent + normalise only); T-129 adds recall-merge + clarifier after lite soaks |
| Free-tier Groq quota load-bearing | T-124 includes Haiku fallback (mirrors T-092 compression pattern) |
| AFK + bubble edge cases | AFK removed; bubble force-flush spec'd for /exit and /clear |
| 5-job thinking prompt produces inconsistency | Phased: 2 jobs in lite, +3 in full |

---

## Risk + mitigation

| Risk | Mitigation |
|---|---|
| 6s bubble feels laggy to fast typers | Tunable env var PI_BUBBLE_IDLE_MS; typing indicator the moment bubble opens |
| Thinking layer doubles latency on normie | Skip on direct commands; cap Groq at 150 tokens; parallel with recall |
| Caretaker mutates memory incorrectly | All mutations soft (invalid_at, superseded_by); never destructive; filelock prevents races |
| Vision provider chain adds cost | Claude Vision only fires on Gemini failure; cooldown prevents thundering retry |
| T-125b false-positive merge | Same-category requirement + 0.92 cosine threshold + dry-run on first deploy |

---

## Success criteria (Phase complete when)

- [ ] `/passive` reports zero entries for `silent_failure_watcher` in categories: `vision.*`, `telegram.bubble.*`, `caretaker.*`, `thinking.*`
- [ ] Sending three `Hey` messages within 6s produces exactly one response
- [ ] Replying to a Pi photo-analysis message recalls the original analysis without re-vision
- [ ] `User is 19` → automatic `User is 20` after their next birthday tick (T-125a)
- [ ] Gemini 429 → graceful Claude Vision fallback with no user-visible error (T-121)
- [ ] `/exit` during open bubble: response sent, THEN goodbye, THEN session ends (T-122 + T-126)
- [ ] `/clear` during open bubble: response sent under old session_id, new session begins (T-122 + T-126)
- [ ] Conversation-mined candidates surface in daily digest (T-127)
- [ ] `scripts/close_ticket.py T-NNN` is the only sanctioned close path (T-128)
- [ ] ADR-007 finalised (T-125c)
- [ ] verify.py PASS at every ticket close

---

## Out of scope (deferred)

- T-115 (Groq tool_use_failed brownout) — still deferred; will benefit from T-121's provider chain pattern
- Multi-peer Telegram (Pi-to-Pi) — original Phase 9 territory
- Web UI / Discord bot — Phase 9+
- Voice bubble (multiple voice messages in rapid succession) — defer until voice usage warrants it
- AFK / queued summary mode — removed by Ash decision
