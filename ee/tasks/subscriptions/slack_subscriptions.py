"""FOSS stub for ee.tasks.subscriptions.slack_subscriptions."""


def _prepare_slack_message(*args, **kwargs) -> dict:
    """Used by tests only. Returns empty Slack message payload."""
    return {}


def deliver_subscription_via_slack(*args, **kwargs) -> None:
    return None


def get_slack_integration_for_team(*args, **kwargs):
    """Upstream returns the team's active Slack Integration row. FOSS: None.

    posthog/temporal/subscriptions/activities.py guards on truthiness, so
    None makes the activity short-circuit and skip Slack delivery."""
    return None


async def send_slack_message_with_integration_async(*args, **kwargs) -> None:
    return None
