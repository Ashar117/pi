"""Response paths — root (Claude tool loop) and normie (Groq, no tools).

Mechanical lift from PiAgent._respond_root and PiAgent._respond_normie (Phase 4)
— no behaviour change. Both functions take the PiAgent instance to access the
shared state (messages, history, memory, evolution, claude/groq clients,
session_id, mode).
"""
import json
from datetime import datetime, timezone
from typing import List


def respond_root(agent, user_input: str, interaction_start, tool_calls_made: list) -> str:
    """Root mode: Claude with full tool loop."""
    system_prompt = agent._get_system_prompt()

    # Append user message to persistent history
    agent.messages.append({"role": "user", "content": user_input})

    agent._truncate_messages_safely(20)

    # Call Claude
    response = agent.claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        system=system_prompt,
        messages=agent.messages,
        tools=agent._get_tool_definitions(),
    )

    # CRITICAL: Append raw assistant response to history FIRST
    agent.messages.append({"role": "assistant", "content": response.content})

    t_in = response.usage.input_tokens if response.usage else 0
    t_out = response.usage.output_tokens if response.usage else 0

    # Agentic loop: keep going while Claude wants to use tools
    while response.stop_reason == "tool_use":
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                result = agent._execute_tool(block.name, block.input)
                tool_calls_made.append({"id": block.id, "name": block.name, "input": block.input})
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result),
                })

        # Add tool results to history
        agent.messages.append({"role": "user", "content": tool_results})

        # Continue conversation
        response = agent.claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=system_prompt,
            messages=agent.messages,
            tools=agent._get_tool_definitions(),
        )

        # Append this response too
        agent.messages.append({"role": "assistant", "content": response.content})

        t_in += response.usage.input_tokens if response.usage else 0
        t_out += response.usage.output_tokens if response.usage else 0

    # Extract final text
    final_text = "".join(
        block.text for block in response.content if hasattr(block, "text")
    )

    # Keep simplified string history for research mode
    agent.history.append({"role": "user", "content": user_input})
    agent.history.append({"role": "assistant", "content": final_text})

    total_cost = agent._calculate_cost(t_in, t_out)
    agent.evolution.log_interaction(
        user_input=user_input,
        pi_response=final_text,
        tool_calls=tool_calls_made,
        success=True,
        mode=agent.mode,
        cost=total_cost,
        model="claude-sonnet-4-6",
        tokens_in=t_in,
        tokens_out=t_out,
        metadata={
            "duration_seconds": (datetime.now(timezone.utc) - interaction_start).total_seconds(),
            "session_id": agent.session_id,
        },
    )

    return final_text


def respond_normie(agent, user_input: str, interaction_start) -> str:
    """Normie mode: Groq, no tools."""
    system_prompt = agent._get_system_prompt()

    # T-016: Build session context from prior messages BEFORE appending the
    # current turn, so it doesn't appear duplicated to Groq.
    session_ctx = agent._extract_text_from_messages(n=10)
    if session_ctx:
        system_prompt += f"\n\nSESSION CONTEXT (read-only, from this conversation):\n{session_ctx}"

    # T-016: Persist the user turn to the unified message store so a later
    # mode switch (normie → root) sees the conversation as one thread.
    agent.messages.append({"role": "user", "content": user_input})

    groq_messages = [{"role": "system", "content": system_prompt}]
    groq_messages.append({"role": "user", "content": user_input})

    try:
        response = agent.groq.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=groq_messages,
            max_tokens=2048,
        )
        content = response.choices[0].message.content
    except Exception as e:
        content = f"[Pi] Groq error: {str(e)}"

    # T-016: Persist assistant turn to unified store as well.
    agent.messages.append({"role": "assistant", "content": content})
    agent._truncate_messages_safely(20)

    agent.history.append({"role": "user", "content": user_input})
    agent.history.append({"role": "assistant", "content": content})

    agent.evolution.log_interaction(
        user_input=user_input,
        pi_response=content,
        tool_calls=[],
        success=True,
        mode=agent.mode,
        cost=0.0,
        model="groq",
        tokens_in=0,
        tokens_out=0,
        metadata={
            "duration_seconds": (datetime.now(timezone.utc) - interaction_start).total_seconds(),
            "session_id": agent.session_id,
        },
    )

    return content
