"""
Collaborative orchestrator — routes messages between agents based on
HANDOFF_TO directives in each agent's response.

No fixed pipeline order. Agents decide who speaks next.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


VALID_AGENTS = {"pm_agent", "dev_agent", "qa_agent", "sre_agent", "em_agent"}
COMPLETE_SIGNAL = "COMPLETE"


@dataclass
class Turn:
    """One agent's contribution to the conversation."""
    round: int
    agent: str
    message: str
    handoff_to: str
    tool_calls: list = field(default_factory=list)

    @property
    def agent_short(self) -> str:
        return self.agent.replace("_agent", "").upper()

    @property
    def is_complete(self) -> bool:
        return self.handoff_to == COMPLETE_SIGNAL


def parse_handoff(response: str) -> tuple[str, str]:
    """
    Extract HANDOFF_TO directive from the end of an agent's response.

    Returns (next_agent_or_COMPLETE, clean_message_without_directive).
    """
    lines = response.strip().splitlines()
    handoff = None

    # Scan from the end for the directive
    clean_lines = []
    for line in reversed(lines):
        stripped = line.strip()
        if stripped.upper().startswith("HANDOFF_TO:"):
            value = stripped.split(":", 1)[1].strip()
            # Normalise: allow "COMPLETE", agent names with or without _agent
            if value.upper() == COMPLETE_SIGNAL:
                handoff = COMPLETE_SIGNAL
            else:
                # Accept both "dev" and "dev_agent"
                candidate = value if value.endswith("_agent") else f"{value}_agent"
                handoff = candidate if candidate in VALID_AGENTS else "em_agent"
        else:
            clean_lines.insert(0, line)

    clean_message = "\n".join(clean_lines).strip()
    # Fallback: if no directive found, escalate to EM
    if handoff is None:
        handoff = "em_agent"

    return handoff, clean_message


def build_agent_prompt(
    agent_name: str,
    feature: str,
    conversation: list[Turn],
    incoming_message: Optional[str] = None,
) -> str:
    """
    Build the prompt for an agent's turn, including full conversation history.
    """
    agent_short = agent_name.replace("_agent", "").upper()

    # Format conversation history
    history = ""
    if conversation:
        history = "\n\n--- CONVERSATION SO FAR ---\n"
        for turn in conversation:
            history += f"\n[{turn.agent_short}]: {turn.message}\n"
        history += "\n--- END OF CONVERSATION ---\n"

    # Direct message to this agent (if routed with a specific message)
    direct = ""
    if incoming_message:
        direct = f"\n\nYou have been specifically called upon because:\n{incoming_message}\n"

    return (
        f"Feature request: {feature}\n"
        f"{history}"
        f"{direct}"
        f"\nIt is now your turn, {agent_short}. "
        f"Read the conversation above and take your next action. "
        f"End your message with HANDOFF_TO: <agent_name> or HANDOFF_TO: COMPLETE."
    )


class CollabOrchestrator:
    """
    Routes messages between CugaAgent instances based on HANDOFF_TO directives.
    Each agent reads the full conversation history and decides who speaks next.
    """

    def __init__(self, agents: dict, max_rounds: int = 12):
        self.agents = agents
        self.max_rounds = max_rounds
        self.conversation: list[Turn] = []

    async def run(self, feature: str, on_turn=None) -> list[Turn]:
        """
        Run the collaborative pipeline.

        Args:
            feature: The feature request to process.
            on_turn: Optional async callback(turn: Turn) called after each round.

        Returns:
            List of Turn objects representing the full conversation.
        """
        current_agent = "pm_agent"
        incoming_message = None

        for round_num in range(1, self.max_rounds + 1):
            agent = self.agents.get(current_agent)
            if agent is None:
                # Unknown agent — escalate to EM
                current_agent = "em_agent"
                continue

            prompt = build_agent_prompt(
                agent_name=current_agent,
                feature=feature,
                conversation=self.conversation,
                incoming_message=incoming_message,
            )

            result = await agent.invoke(
                prompt,
                thread_id=f"collab-{current_agent}-{round_num}",
                track_tool_calls=True,
            )

            raw_response = str(result)
            tool_calls = getattr(result, "tool_calls", []) or []

            handoff_to, clean_message = parse_handoff(raw_response)

            turn = Turn(
                round=round_num,
                agent=current_agent,
                message=clean_message,
                handoff_to=handoff_to,
                tool_calls=tool_calls,
            )
            self.conversation.append(turn)

            if on_turn:
                await on_turn(turn)

            if turn.is_complete:
                break

            current_agent = handoff_to
            incoming_message = None  # clear — next agent reads full history

        return self.conversation
