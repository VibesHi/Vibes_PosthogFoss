"""FOSS stub. Scheduled email delivery of dashboards is EE feature; noop."""


def send_email_subscription_report(*args, **kwargs) -> None:
    return None


send_email_subscription_report.delay = lambda *a, **kw: None  # type: ignore[attr-defined]
