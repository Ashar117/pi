# docs/CHANGELOG.md

One line per session that modifies a runtime file. Paste the last 10 lines into any new Claude chat's first turn for shared ground truth.

Format: `YYYY-MM-DD | file | summary`

---

2026-04-21 | pi_agent.py | Fix T-006/T-007: session state cleared on mode switch; exit summary skipped
2026-04-21 | tools/tools_memory.py | Fix T-008/T-009: L1 tier implemented; mode switch loosened to set membership
2026-04-22 | tools/tools_memory.py | Fix T-010/T-011/T-014: dynamic L3 categories; sync TTL 300s; dual-store write verification
2026-04-22 | pi_agent.py | Fix T-012/T-013: safe message truncation; session_id added throughout
2026-04-24 | pi_agent.py | Fix T-015: loose mode-switch matcher (natural-language variants)
2026-04-24 | pi_agent.py | Fix T-016: normie mode now appends to self.messages for cross-mode continuity
2026-04-25 | evolution.py | Fix SM-001/T-020: tools_used/tool_calls field drift; session_id at top level
2026-04-25 | docs/* | Phase 1: 12 stale root docs archived; canonical ARCHITECTURE.md + USER_GUIDE.md; README/ABOUT rewritten
2026-04-26 | tools/tools_memory.py | Fix T-017: docstring corrected (tier=None excludes L1); Fix T-021: L2 content search (two-query merge)
2026-04-26 | pi_agent.py + agent/ | Phase 4: pi_agent.py modular refactor into agent/ package (behaviour-identical)
2026-05-02 | prompts/consciousness.txt | Phase 5: ghost web_search removed; memory_read query rules added; normie refusal table added
2026-05-02 | prompts/system.txt | Phase 5: complete rewrite — 19 vague lines → structured normie context with tool list and refusal table
2026-05-02 | scripts/verify.py | Phase 6: CI script — syntax check + non-costly tests + docs/STATUS.md write
2026-05-02 | docs/CONTRIBUTING.md | Phase 6: engineering loop protocol documented
2026-05-02 | docs/templates/ | Phase 6: TICKET, SOLUTION, LESSON, MODULE templates added
2026-05-02 | tools/tools_memory.py | Fix SM-005: L2/L1 memory_write now returns verified=True so tool tracking is accurate
2026-05-02 | pi_agent.py | Fix T-022: sys.stdout.reconfigure(utf-8) so box-drawing chars work on Windows cp1252
2026-05-02 | testing/test_memory_tool_path.py | T-023: L2-only round-trip test that forces memory_read tool path (costly)
2026-06-13 | tools/tools_memory.py | T-186: add conversations + conversation_turns SQLite tables; UNIQUE(conversation_id, idx) idempotency; resume/chats REPL commands
2026-06-13 | tools/tools_memory.py + pi_agent.py | T-205: close_conversation stores digest; recall_episode tool + prefetch triggers for episodic context injection
2026-06-13 | app/server.py + pi_daemon.py + requirements.txt | T-187: FastAPI brain server on 127.0.0.1:7712; Bearer auth; asyncio.Lock FIFO; SSE streaming; CORS for chrome-extension:// origins
2026-06-13 | agent/conversation.py + tools/tools_telegram.py | T-188: conversation_switch context manager; Telegram peer routing per chat_id via telegram:<id> conversation IDs
2026-06-13 | agent/watchers.py | T-206: watchers v2 — analyze=True flag routes events through dedicated 'watchers' conversation; 6/hour rolling rate limit
2026-06-13 | web/index.html + web/chat.js + app/server.py | T-189: dark web chat UI at GET /; shared chat.js (getToken, authHeaders, sendChat, streamChat, buildPageContextPrefix)
2026-06-13 | extension/manifest.json + extension/sw.js + extension/sidepanel.html | T-190: Chrome MV3 extension — side panel + "Ask Pi about this page" context menu; reuses web/chat.js
2026-06-14 | agent/storage.py + tools/tools_memory.py | T-165: StorageBackend seam — SQLiteStorageBackend + InMemoryStorageBackend; MemoryTools._sqlite_backend wired; all conversation methods use backend
2026-06-14 | agent/awareness_cache.py + pi_agent.py | T-173: AwarenessCache extracted from PiAgent; 6 _awareness_* attrs → _awareness_cache; property delegates; test_awareness_atomic.py updated
2026-07-06 | 20-ticket batch (T-251..T-275) | Hackathon prep: silent-failure telemetry (telegram+media), email watcher + Telegram triage buttons, turns.jsonl rotation, GitHub Actions CI, handler tests, deep_debate tool, P1 alerting, Gemini image backend, bare-except verify gate; bugs fixed: L3 sync truncation (T-270), gmail_send now draft-only (T-271), watcher→Telegram wiring (T-274), test encoding (T-275)
2026-07-07 | all hand-docs | Full doc rewrite: PI.md/CLAUDE.md/README/ABOUT/ARCHITECTURE v4/USER_GUIDE/CONTRIBUTING/FEATURE_LIST/PI_CONTROL/PROJECT_MAP refreshed against code; UPGRADE_PLAN + PI_ENGINEERING_LAYOUT + PHASE_8.8_CARETAKER archived to docs/_archive/2026-07-07/
2026-07-10..12 | agent/conversation.py + pi_agent.py + tools/tools_memory.py | T-161/T-162/T-163: canonical Turn/message_text schema; self.history duplicate state removed; 4 memory L3 read paths unified behind _l3_active_records
2026-07-12 | agent/modes.py + pi_agent.py + core/llm_router.py + scripts/sprint.py + 15 more | God mode fully removed from active code: private layer archived to _god_archive/ (gitignored), all mode/tool/router/sprint isolation code deleted, docs/tests updated; profile namespace machinery (general-purpose) kept intact
2026-07-10..17 | core/providers/qwen.py + core/llm_router.py + app/config.py + memory/semantic_dedup.py | Qwen Cloud (Alibaba DashScope) integration for Global AI Hackathon Track 1: chat + text-embedding-v3 provider, qwen-first tier ordering, LICENSE (Apache-2.0), public default prompt fallback, README/deploy docs, PI_SERVER_HOST + mandatory token gate for non-localhost binds
2026-07-13..17 | tools/tools_memory.py + pi_agent.py + agent/session.py | T-290..297: hybrid dense-cosine + BM25 memory retriever (MemoryTools.retrieve), wired into the turn loop replacing single-keyword prefetch; L3 embedding column + session-exit backfill; session summary routed through LLMRouter tier='cheap'
2026-07-17 | tools/tools_memory.py + agent/retention.py + agent/caretaker.py + scripts/memory_cli.py | T-298..303: timely-forgetting batch — dense retrieve() no longer resurrects expired facts; ephemeral phrasing ("just for today") auto-infers expiry; decay-archive default-on + daily; semantic "forget about X" via retrieve(); `memory_cli forgotten` lifecycle ledger; Qwen-adjudicated implication-level contradiction scan (tier='cheap', capped, event-driven)
2026-07-17 | agent/awareness_shortcut.py | T-295/T-296: markets awareness shortcut no longer answers out-of-scope instrument questions (wheat/gold/forex) with the cached crypto line; eth/bitcoin added as market signals; crypto-only snapshot declines general/equity questions
