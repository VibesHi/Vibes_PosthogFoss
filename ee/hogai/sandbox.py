"""FOSS stub for ee.hogai.sandbox.

Constants used by products/tasks/backend/temporal/process_task/activities/
send_followup_to_sandbox.py which is part of the Tasks AI sandbox feature.
"""

STOP_REASON_END_TURN = "end_turn"
TURN_COMPLETE_METHOD = "turn_complete"


def is_turn_complete(*args, **kwargs) -> bool:
    """Upstream inspects an LLM event to decide if the agent's turn ended.
    FOSS: agent never runs, so any caller treating False as 'keep going'
    will silently exit the loop on first iteration."""
    return False
