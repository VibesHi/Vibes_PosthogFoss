"""FOSS stub for ee.tasks.subscriptions package init.

posthog/temporal/subscriptions/activities.py imports SLACK_USER_CONFIG_ERRORS,
SUPPORTED_TARGET_TYPES and _capture_delivery_failed_event from this package
(at module top), which means they need to exist on import even though the
underlying activities never fire on FOSS (no scheduled subscriptions).
"""


SUPPORTED_TARGET_TYPES = ("email", "slack")

# Slack error strings upstream pattern-matches against. Empty tuple means
# `error in SLACK_USER_CONFIG_ERRORS` is always False -> activities never
# treat anything as a user-config error.
SLACK_USER_CONFIG_ERRORS: tuple[str, ...] = ()


def _capture_delivery_failed_event(*args, **kwargs) -> None:
    return None
