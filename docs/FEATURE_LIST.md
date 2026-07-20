# Pi — Comprehensive Feature List & Capability Registry

**Last updated:** 2026-07-07  
**Owner:** Ash  
**Purpose:** Single source of truth for every feature — built, in-flight, queued, and brainstormed. Prevents drift. When something gets ticketed, update Status here. When something ships, mark ✅.

---

## Status Key

| Symbol | Meaning |
|--------|---------|
| ✅ | Built and verified (`verify.py` PASS) |
| 🟡 | Built but not fully verified end-to-end |
| 🔄 | In progress / active sprint |
| 📋 | Queued — ticket exists or is ready to file |
| 💡 | Brainstormed — no ticket yet; needs scoping |
| ❌ | Broken — known regression |

---

## Priority Key

**P0** = blocks daily use · **P1** = high value, no workaround · **P2** = meaningful improvement · **P3** = polish / nice-to-have

---

## 1. Core Engine

| ID | Feature | Status | Priority | Notes |
|----|---------|--------|----------|-------|
| C-001 | Root mode (Claude Sonnet 4.6, full tool loop) | ✅ | — | `pi_agent.py:454-482` |
| C-002 | Normie mode (Groq llama-3.3-70b, snappy chat) | ✅ | — | Free tier |
| C-004 | Research mode (3-agent debate: Claude/Groq/Gemini) | ✅ | — | `pi_agent.py:373-397` |
| C-005 | Natural-language mode switching | ✅ | — | T-009, T-015 closed |
| C-006 | Cost awareness + daily budget gate | ✅ | — | $0.50 default; auto-downgrades to normie |
| C-007 | Session ID correlation across logs | ✅ | — | |
| C-008 | 3-line compact startup banner | ✅ | — | T-041 closed |
| C-009 | Autonomous sprint runner (`scripts/sprint.py`) | 🟡 | — | Built + fully gated, but **zero production closes** — first real close is T-256, waiting on a genuine candidate ticket |
| C-010 | **High-thinking mode** | 💡 | P1 | Dedicated mode using Claude extended thinking (budget_tokens configurable). For architecture decisions, hard debugging, thesis analysis. Costs more — gate behind explicit trigger ("think hard", "deep mode"). |
| C-011 | **Workflow builder** | 💡 | P3 | Rejected at the 2026-07 hackathon audit: Pi already demos multi-system workflows without a planner; a generic DAG orchestrator is speculative infra. Stays brainstormed — revisit only with a concrete workflow that can't be done otherwise. |
| C-012 | **Partner mode (no yes-man)** | 💡 | P1 | Dedicated behavioral flag in `consciousness.txt`. Pi has an opinion. If Ash's plan has a flaw, Pi says so with evidence, not agreement. Proposes alternatives. Will disagree and explain why. Only backs down when Ash presents counter-evidence, not just insistence. Distinct from research-mode — this is Pi's default stance, not a separate model call. |

---

## 2. Memory System

| ID | Feature | Status | Priority | Notes |
|----|---------|--------|----------|-------|
| M-001 | L1 raw log (turns.jsonl + Supabase raw_wiki) | ✅ | — | T-024, T-039 closed |
| M-002 | L2 organized memory (Supabase, distilled facts) | ✅ | — | |
| M-003 | L3 fast-recall cache (SQLite, injected into prompt) | ✅ | — | |
| M-004 | memory_read / memory_write / memory_delete tools | ✅ | — | |
| M-005 | Semantic dedup on L3 write (word-overlap fingerprint) | ✅ | — | T-035 closed |
| M-006 | Conflict detection on L3 write | ✅ | — | T-035 closed |
| M-007 | Structured profile (L3 profile_structured category) | ✅ | — | T-026c closed |
| M-008 | Vault sync (one-way mirror to Obsidian on exit) | ✅ | — | T-033 closed |
| M-009 | Inferred-vs-stated fact tagging | ✅ | — | T-031 closed |
| M-010 | **Episodic recall** | ✅ | P1 | `close_conversation(digest)` stores a session summary; `recall_episode(query)` keyword-searches all conversation digests. Prefetch triggers inject past context automatically. (T-205) |
| M-014 | **Multi-conversation persistence** | ✅ | P1 | `conversations` + `conversation_turns` SQLite tables; REPL `resume`/`chats` commands; idempotent INSERT OR IGNORE. (T-186) |
| M-015 | **StorageBackend seam** | ✅ | P2 | `SQLiteStorageBackend` + `InMemoryStorageBackend` decouple tier logic from transport; injected in `MemoryTools.__init__`. (T-165) |
| M-011 | **Memory health auditor** | 💡 | P2 | Weekly job: duplicate rate, staleness score, conflict density, category imbalance. Outputs health report + pruning candidates. Ash approves, Pi deletes. Surfaces in weekly retro. |
| M-012 | **Auto-promotion throttle** | 💡 | P2 | Only promote L2→L3 on importance ≥ 7 OR referenced ≥ 2 times in session. Prevents L3 pollution. Extends T-029 fix. |
| M-013 | **Memory source lineage** | 💡 | P3 | Every L2/L3 entry carries: source (stated/inferred_confirmed), session_id it came from, number of times accessed. Full provenance — lets Pi explain why it believes something. |
| M-016 | **Hybrid dense + lexical retrieval** | ✅ | — | `MemoryTools.retrieve()` fuses cosine (Qwen/Gemini embeddings) with BM25 across L3+L2; replaces single-keyword `_prefetch_memory`. Proven on a paraphrase case lexical-only search misses entirely. (T-292/T-293) |
| M-017 | **L3 embeddings + backfill** | ✅ | — | `embedding` column on `l3_cache`; filled at session exit (`backfill_l3_embeddings`), not inline on write, to keep the interactive write path fast. (T-291/T-297) |
| M-018 | **Auto-inferred expiry** | ✅ | — | Ephemeral phrasing ("just for today", "until friday") auto-sets `active_until` via a deterministic phrase table — no ISO datetime required from the model. (T-299) |
| M-019 | **Decay-archive default-on** | ✅ | — | Ebbinghaus neglect decay (T-135) now runs daily, opt-out via `PI_DECAY_ARCHIVE=off`, instead of opt-in/weekly. Soft-archive, pinned-immune. (T-300) |
| M-020 | **Semantic forget** | ✅ | — | `memory_delete` / `memory_cli forget` route non-ID targets through `retrieve()`, unioned with the existing lexical match set — finds related memories with zero shared words, all existing safety guards preserved. (T-302) |
| M-021 | **Forgetting ledger** | ✅ | — | `memory_cli forgotten [--days N]` — one command shows what was forgotten, when, and why (EXPIRED/CONTRADICTED/MERGED), with deterministic precedence. (T-301) |
| M-022 | **LLM-adjudicated contradiction curation** | ✅ | — | `scan_semantic_contradictions` cosine-prefilters + Qwen (tier='cheap') adjudicates implication-level conflicts the lexical topic-key scan can't see; capped, event-driven (session exit / daily cron), never a background daemon. (T-303) |

---

## 3. Intelligence & Reasoning

| ID | Feature | Status | Priority | Notes |
|----|---------|--------|----------|-------|
| I-001 | Multi-agent debate (research mode) | ✅ | — | 3 models debate a single question |
| I-002 | Self-introspection tool (`system_introspect`) | ✅ | — | T-028 closed |
| I-003 | SOLUTIONS.jsonl pattern matching (self-healing) | 💡 | P2 | Original `SelfModifier` was archived (T-088/R7) — this needs a re-scope from scratch, likely as a sprint.py pre-escalation search over SOLUTIONS.jsonl |
| I-009 | `deep_debate` — research debate as a root tool | ✅ | — | 3-agent debate callable mid-conversation without mode switch (T-262) |
| I-004 | **Anti-hallucination protocol** | 💡 | P0 | Pi must cite its source for every factual claim: memory tier (L1/L2/L3 + entry id), tool result, or explicit "I don't have a stored fact for this — this is my best guess." Confidence score (high/medium/low) required on uncertain claims. If Pi can't ground a claim, it says so rather than generating plausible-sounding content. Implemented as a hard rule in `consciousness.txt` + a `cite_fact` helper that wraps memory reads. |
| I-005 | **Complexity estimator for tickets** | 💡 | P2 | Before filing a ticket, Pi scores it S/M/L/XL by comparing to closed tickets (TF-IDF similarity over SOLUTIONS.jsonl). Improves sprint planning. Surfaces in `plan_sprint.py`. |
| I-006 | **Disagreement engine (partner logic)** | 💡 | P1 | Separate from C-012 (the persona). The mechanism: Pi maintains a list of active "assertions" Ash has made this session. On each new message, Pi checks if the request contradicts a prior assertion or a stored fact. If yes, flags it before executing. "You said X earlier — you're now asking for Y which conflicts. Which should I follow?" |
| I-007 | **Confidence-gated execution** | 💡 | P2 | For tool calls with side effects (memory_write, file edit, email_send), Pi scores its own confidence in the action (0–1). Below 0.7, Pi asks before executing. Above 0.7, proceeds and logs confidence. Reduces wrong writes and ghost actions. |
| I-008 | **Self-performance reflection on startup** | 📋 | P2 | L3 active_project already flags this. On startup, Pi reads last N session stats from evolution.jsonl and surfaces one insight: "Last 3 sessions: avg cost $0.34, memory_write most used tool, 2 L3 dedup hits. One thing I can do better: fewer redundant awareness fetches." |

---

## 4. Code & Creation

| ID | Feature | Status | Priority | Notes |
|----|---------|--------|----------|-------|
| K-001 | execute_python / execute_bash tools | ✅ | — | With 30s timeout, sandbox |
| K-002 | read_file / modify_file / create_file tools | ✅ | — | |
| K-003 | search_codebase tool | ✅ | — | |
| K-004 | image_gen tool | ✅ | — | Three backends: pollinations (default, free) · HF FLUX-schnell · Gemini Imagen (T-268) |
| K-005 | **Code generation mode** | 💡 | P1 | Dedicated prompt block for code tasks: Pi thinks step-by-step before writing code, writes tests alongside implementation, runs the code in sandbox before returning it, reports actual output not assumed output. "write me X" → plan → code → test → verify → return. Never returns untested code. |
| K-006 | **Image generation — enhanced** | 💡 | P2 | Current `image_gen` is a single call. Expand to: style presets (realistic, diagram, diagram-dark, sketch), aspect ratio selection, batch generation with variation seeds, auto-save to `data/generated/` with metadata logged to evolution.jsonl. |
| K-007 | **Code-to-paper bridge** | 💡 | P2 | Given a Python experiment file + results, Pi generates a LaTeX/markdown paper section: method, pseudocode, results table. Useful for GNN research writeups. |
| K-008 | **Auto-PR description generator** | 📋 | P2 | `sprint.py` commits to a branch → Pi generates structured PR: what changed, why, test plan, risk flags. Extends T-043 deliverable. `gh pr create` auto-call. |
| K-009 | **Whiteboard → vault pipeline** | 💡 | P2 | Photo of whiteboard (GNN diagram, math) → `ocr_image` + `analyze_image` → formatted markdown note in vault. Bridge for research sketches. |

---

## 5. Awareness & Life Intel

| ID | Feature | Status | Priority | Notes |
|----|---------|--------|----------|-------|
| A-001 | get_weather (snapshot at startup) | ✅ | — | |
| A-002 | get_news / get_stocks / get_tech_updates | ✅ | — | |
| A-003 | daily_briefing tool | ✅ | — | |
| A-004 | refresh_awareness tool | ✅ | — | |
| A-005 | Awareness snapshot shortcircuit (answer from cache before LLM call) | ✅ | — | T-030 closed |
| A-006 | **"What did I miss?" morning briefing** | 💡 | P1 | One command at day start: emails since last session, papers matching research interests, news, background Pi jobs that completed, calendar for today, open tickets. Uses existing tools — needs orchestration wrapper. Output in 15 lines max. |
| A-007 | **Prayer time integration** | 💡 | P1 | Adhan API for Fajr/Dhuhr/Asr/Maghrib/Isha, location-aware. Telegram reminder 5 min before. Pi pauses non-urgent sprint tasks around prayer windows. Non-negotiable filter alignment. |
| A-008 | **Dietary-preference food radar** | 💡 | P2 | Google Maps API + filter logic for user-configured dietary constraints (e.g. specific halal certification standards). "Find me dinner" → ranked list by distance, open now, with method noted. |
| A-009 | **Flight deal watcher** | 💡 | P2 | Background job (APScheduler) watching user-configured home/destination routes. Price threshold alert via Telegram. |
| A-010 | **Academic opportunity radar** | 💡 | P1 | Monitors: research-area conference deadlines (NeurIPS, ICML, ICLR, KDD, WWW), visa/work-authorization windows, semester dates. Pi files calendar events and sends Telegram alerts 30 days out. Undergrad researchers constantly miss these. |
| A-011 | **Budget tracker** | 💡 | P2 | Pi tracks spending mentioned in conversation. Student-focused: flight deals, textbooks, ATM fees. Monthly summary in retro. Always checks for student discounts first (already a stored preference). |
| A-012 | **Mood-aware responses** | 💡 | P3 | From session patterns (message length, response speed, word choice), Pi infers stress level. If overloaded, Pi suggests a break or prayer before diving into a task. Stored as session-ephemeral state, not persisted to L3. |

---

## 6. Communication & Output

| ID | Feature | Status | Priority | Notes |
|----|---------|--------|----------|-------|
| O-001 | telegram_send (one-way notifications) | ✅ | — | |
| O-002 | speak (TTS output) | ✅ | — | |
| O-003 | gmail_inbox / gmail_search / gmail_read / gmail_send | ✅ | — | `gmail_send` is **draft-only by construction** — it cannot send (T-271) |
| O-013 | **Email triage HITL flow** | ✅ | P1 | Gmail unread watcher → Telegram buttons (Draft reply / Add to calendar / Ignore) → Gmail draft or Calendar event (T-257/T-258) |
| O-004 | calendar_today / upcoming / search / create / delete | ✅ | — | |
| O-005 | **Telegram ↔ Pi bidirectional conversation** | ✅ | P1 | Full conversation mode via Telegram. Each chat_id gets its own isolated conversation via `conversation_switch`. Ash sends tasks from phone; Pi processes and replies with full tool access. (T-188) |
| O-010 | **Brain server (HTTP + SSE)** | ✅ | P1 | FastAPI on 127.0.0.1:7712. Bearer token auth (`PI_HTTP_TOKEN`). `POST /chat` + `GET /chat/stream` (SSE). `GET /conversations`. FIFO `asyncio.Lock`. (T-187) |
| O-011 | **Web chat UI** | ✅ | P2 | Dark single-page app served at `GET /`. Conversation sidebar, SSE token streaming, mode badge. Token in localStorage. Shared `web/chat.js` reused by extension. (T-189) |
| O-012 | **Chrome MV3 browser extension** | ✅ | P2 | Side panel hosts the chat UI. Context-menu "Ask Pi about this page" captures selection/URL/title and prefixes the message. No content scripts beyond selection capture. (T-190) |
| O-006 | **Weekly digest email** | 💡 | P2 | Every Sunday: research progress, tickets closed, memory highlights, upcoming week priorities. Auto-generated by `retro.py` extension + `gmail_send`. |
| O-007 | **Professor email drafter** | 💡 | P2 | Given a goal (research ask, recommendation letter request, advisor reply), Pi drafts email in Ash's voice (learned from session history). Direct but professional tone. One `gmail_send` away from delivery. |
| O-008 | **Discord server bot** | 💡 | P3 | Rejected at the 2026-07 hackathon audit — Telegram already proves the channel story with depth; Discord adds days of work for no new narrative. Recipe if ever needed: clone the `telegram:<chat_id>` conversation-id pattern. |
| O-009 | **Voice memo → note** | 💡 | P2 | Send voice memo via Telegram → Groq Whisper transcription → structured Obsidian note. Bridges Phase 8 voice work into something usable now via existing Telegram integration. |

---

## 7. Research & Academic

| ID | Feature | Status | Priority | Notes |
|----|---------|--------|----------|-------|
| R-001 | scholar_search tool | ✅ | — | |
| R-002 | web_search / web_browse / reddit tools | ✅ | — | |
| R-003 | **GNN research mode** | 💡 | P1 | Dedicated mode combining: scholar_search → ArXiv papers → structured vault note → Telegram digest. Tracks Ash's own experiments (reads from a research log in vault). Auto-generates related work summaries. Feeds thesis writing. |
| R-004 | **Paper tracker (async background)** | 💡 | P1 | APScheduler job: nightly ArXiv keyword feed (graph neural networks, link prediction, heterogeneous graphs, GNN expressivity). Surfaces new papers to Telegram each morning with 3-line summaries. Marks what Ash has already read (stored in L3). |
| R-005 | **PDF annotator** | 💡 | P2 | Upload a paper PDF → Pi returns: abstract distillation, key contributions, methodology, open questions, citation-ready BibTeX. Saves to vault as per-paper note. Uses existing `analyze_document_smart` tool. |
| R-006 | **Experiment tracker** | 💡 | P2 | Pi tracks GNN experiment runs logged in conversation (hyperparams, dataset, metric). Stores in vault. "Compare my last 3 runs" → table. Points out which variables changed between runs. |

---

## 8. Documents & Multimodal

| ID | Feature | Status | Priority | Notes |
|----|---------|--------|----------|-------|
| D-001 | read_document / analyze_document_smart | ✅ | — | |
| D-002 | analyze_image / analyze_images / analyze_video | ✅ | — | |
| D-003 | ocr_image | ✅ | — | |
| D-004 | detect_faces / recognize_face / register_face | ✅ | — | |
| D-005 | **Lecture recorder → structured note** | 💡 | P2 | Record audio of a lecture → Groq Whisper transcription → Pi summarizes key points + generates Obsidian note with: summary, key terms, action items, related vault notes. Phase 8 STT groundwork. |

---

## 9. Autonomy & Engineering Loop

| ID | Feature | Status | Priority | Notes |
|----|---------|--------|----------|-------|
| E-001 | Engineering loop (ticket → test → fix → verify → close → refresh) | ✅ | — | Core discipline |
| E-002 | `scripts/verify.py` CI gate | ✅ | — | Syntax check + bare-`except:` lint + full non-costly suite; also runs on GitHub Actions on every push (T-260, T-273) |
| E-013 | **Runtime P1 error alerting via Telegram** | ✅ | P2 | P1-class rows in `silent_failures.db` push one throttled, deduped Telegram alert/day (T-265) |
| E-014 | **Silent-failure telemetry** | ✅ | — | `track_silent()` → `data/silent_failures.db`; watched by a passive skill; feeds the data-driven round-2 cleanup (T-264, open) |
| E-003 | `scripts/refresh_pi.py` auto-regenerates PI.md §4/§7/§8/§9 | ✅ | — | T-042 closed |
| E-004 | `scripts/sprint.py` autonomous ticket runner | ✅ | — | T-043 closed |
| E-005 | `scripts/plan_sprint.py` weekly planning ritual | ✅ | — | T-044 closed |
| E-006 | `scripts/retro.py` Friday auto-retro → vault + Telegram | ✅ | — | T-045 closed |
| E-007 | SOLUTIONS.jsonl pattern-match self-healing | 🟡 | P1 | `SelfModifier` exists; not hooked into sprint.py yet. Sprint should search SOLUTIONS before escalating to Ash. |
| E-008 | **Test coverage delta guardian** | 💡 | P2 | Every sprint run checks coverage.py delta. If coverage dropped, auto-files a ticket before closing the current one. Keeps the test floor from eroding silently. |
| E-009 | **Skill evolution radar** | 💡 | P2 | Pi tracks its own metrics: ticket close rate by category, avg time-to-close, tool usage frequency, cost per ticket. Weekly chart in vault. Pi can say "I'm getting slower on memory bugs — here's the data." |
| E-010 | **Health monitor daemon** | 💡 | P2 | Background process (Windows service or Task Scheduler): every 30 min, checks verify.py still passes, disk not full, no runaway Python processes. Telegram alert on failure. Pi as its own ops monitor. |
| E-011 | **Hot-reload for tools** | 💡 | P3 | During dev, Pi detects file changes in `tools/` and reloads without restart. Uses `watchdog`. Saves dev cycle time. |
| E-012 | **Tool performance profiler** | 💡 | P3 | Every tool call logs latency + token cost. Weekly report: top 5 slowest tools, top 5 most expensive. Informs optimization. Extends `evolution.jsonl` schema. |

---

## 10. Identity & Personality

| ID | Feature | Status | Priority | Notes |
|----|---------|--------|----------|-------|
| P-001 | Islamic conduct filter (non-negotiable core value) | ✅ | — | In `consciousness.txt`; overrides user commands |
| P-002 | Cost-conscious student mode (check student discounts first) | ✅ | — | Stored in L3 preferences |
| P-003 | Stable identity across sessions, models, and rewrites | ✅ | — | `consciousness.txt` versioned |
| P-004 | **Pi's daily journal** | 💡 | P3 | Every session exit, Pi appends 3-line journal entry to `vault/.pi-journal/YYYY-MM.md`: what was interesting, what it's uncertain about, what it wants to improve. Gives Pi genuine character growth over time, not just task completion metrics. |
| P-005 | **"What is Pi good at?" introspection** | 📋 | P2 | Extends `system_introspect` (T-028). Pi can explain its capability map in plain English: which tools it's used in the last 30 days, which it hasn't touched, cost per capability area. Self-knowledge as a navigable surface. |
| P-006 | **Capability registry (`capabilities.json`)** | 💡 | P2 | Central file listing every tool: name, mode availability, cost per call, status (working/broken/untested), last used. `system_introspect` reads from it. New tools add a row. Replaces scattered tool lists in PI.md §7. |

---

## 11. Phase Roadmap (future phases)

These are the committed future phases from PI.md §12. Features above map to them.

| Phase | Theme | Key Deliverables | Features |
|-------|-------|-----------------|---------|
| **8 ◐** | Voice | Code-complete (STT, wake word, audio I/O) but never run live — no working audio input on the dev box; see docs/VOICE_LOOP_STATUS.md (T-267) | D-005, O-009 |
| **9 ✅** | Distributed | Brain server (FastAPI/SSE), web chat UI, Chrome MV3 extension, Telegram peer, multi-conv persistence, episodic recall, watchers v2, StorageBackend seam, AwarenessCache | O-005, O-010, O-011, O-012, M-010, M-014, M-015 |
| **10** | Multi-agent | Agent role abstractions, routing layer, shared scratchpad | C-004 extended |
| **11** | Research OS | GNN mode, paper tracker, experiment tracker, PDF annotator | R-003, R-004, R-005, R-006 |
| **12** | Life OS | Prayer times, halal radar, flight deals, academic opportunities, morning briefing | A-006 through A-011 |

---

## Quick Wins (no new architecture needed, just wiring)

These can each be a single ticket, resolved in <2 hours:

| ID | Feature | Why fast |
|----|---------|---------|
| A-006 | "What did I miss?" morning briefing | Orchestrates existing tools (gmail, news, calendar, vault) |
| I-004 | Anti-hallucination citation protocol | `consciousness.txt` change + `cite_fact` helper around memory reads |
| C-012 | Partner mode (no yes-man) | `consciousness.txt` behavioral block; no new tools |
| A-007 | Prayer time Telegram reminders | Adhan API (one HTTP call) + existing `telegram_send` + APScheduler |
| E-007 | Self-healing sprint via SOLUTIONS.jsonl | One function added to `sprint.py`; data already exists |
| K-005 | Code generation mode | `consciousness.txt` block + sandbox verify step already exists |

---

## Backlog Discipline

- When a feature moves to active work: open a ticket in `tickets/open/T-NNN-slug.json`, update Status here.
- When a ticket closes: update Status to ✅, add closed ticket ID in Notes.
- When `refresh_pi.py` runs, it may auto-update §8 in PI.md — this file is separate and must be updated manually.
- Never add a feature here without a one-line description of what done looks like.

---

*This file is the canonical feature registry. PI.md §3 is the active sprint. `PI_ENGINEERING_PLAN.md` is the historical Phase 7 plan. This file spans all phases.*
