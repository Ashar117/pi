"""Session lifecycle helpers — summary generation (Groq) and exit handler.

Mechanical lift from PiAgent._generate_session_summary and the EXIT branch
of PiAgent.run() (Phase 4) — no behaviour change.
"""
from datetime import datetime, timezone
from typing import List, Dict

from agent.truncation import extract_text_from_messages


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

    recent = agent.evolution.get_recent_interactions(hours=24)
    total_cost = sum(i.get("cost", 0) for i in recent)
    if total_cost > 0:
        print(f"[Session Cost: ${total_cost:.4f}]")
