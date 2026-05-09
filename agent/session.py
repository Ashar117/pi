"""Session lifecycle helpers — summary generation (Groq) and exit handler."""
from datetime import datetime, timezone
from typing import List, Dict

from agent.truncation import extract_text_from_messages
from memory.pipeline import distill_session
from tools.tools_obsidian import sync_vault


def generate_session_summary(
    groq_client,
    messages: List[Dict],
    history: List[Dict],
    n: int = 12,
) -> str:
    """Summarize the session via Groq. Falls back to history if messages empty."""
    try:
        context = extract_text_from_messages(messages, n=n)

        # Fallback: use string-only history if messages gave nothing
        if not context and history:
            lines = []
            for h in history[-n:]:
                lines.append(f"{h['role']}: {str(h.get('content', ''))[:300]}")
            context = "\n".join(lines)

        if not context:
            return ""

        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{
                "role": "user",
                "content": (
                    "Summarize this conversation in 2-3 sentences for future "
                    f"reference:\n\n{context}"
                ),
            }],
            max_tokens=150,
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"[Pi] Summary generation failed: {e}")
        return ""


def on_exit(agent) -> None:
    """Handle the EXIT command: write session summary to L3, print session cost.

    Operates on the PiAgent instance so it preserves the original scoped logic
    (memory writes carry session_id; cost reads from evolution tracker).
    """
    print("[Pi] Shutting down...")

    # Write session summary before exit
    if agent.messages or agent.history:
        summary = agent._generate_session_summary()
        if summary:
            agent.memory.memory_write(
                content=(
                    f"Session summary ("
                    f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}): "
                    f"{summary}"
                ),
                tier="l3",
                importance=4,
                category="session_history",
                session_id=agent.session_id,
            )
            print("[Memory] Session summary saved")

    # L1 -> L2 distillation: extract durable facts from this session's archive.
    # Runs only if we have an L1 thread (root mode turns populate it).
    if getattr(agent, "l1_thread_id", None):
        try:
            distill_session(
                thread_id=agent.l1_thread_id,
                session_id=agent.session_id,
                memory_tools=agent.memory,
                groq_client=agent.groq,
            )
        except Exception as e:
            print(f"[Memory] Distillation failed (non-fatal): {e}")

    # L1 is permanent — no pruning. Full raw history is kept forever.

    # L2 -> L3 promotion: elevate high-importance (>=8) L2 facts to ambient context.
    # Runs after distillation so facts written this session are eligible immediately.
    try:
        agent.memory.promote_l2_to_l3(importance_threshold=8)
    except Exception as e:
        print(f"[Memory] L2->L3 promotion failed (non-fatal): {e}")

    # L3 expired entry cleanup: remove past-active_until rows from Supabase + SQLite.
    try:
        agent.memory.prune_l3_expired()
    except Exception as e:
        print(f"[Memory] L3 prune failed (non-fatal): {e}")

    # Vault sync: mirror L3, L2, tickets, and status to vault/ for Obsidian.
    try:
        sync_vault(agent.memory)
    except Exception as e:
        print(f"[Vault] sync failed (non-fatal): {e}")

    recent = agent.evolution.get_recent_interactions(hours=24)
    total_cost = sum(i.get("cost", 0) for i in recent)
    if total_cost > 0:
        print(f"[Session Cost: ${total_cost:.4f}]")
