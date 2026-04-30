"""FOSS stub for ee.tasks.subscriptions.slack_subscriptions."""


def _prepare_slack_message(*args, **kwargs) -> dict:
    """Used by tests only. Returns empty Slack message payload."""
    return {}


def deliver_subscription_via_slack(*args, **kwargs) -> None:
    return None
