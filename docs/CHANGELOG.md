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
