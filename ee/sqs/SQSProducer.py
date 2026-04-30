"""FOSS stub for ee.sqs.SQSProducer.

Used in posthog/tasks/usage_report.py and posthog/temporal/usage_reports/run_usage_reports.py
to publish usage events to SQS for the billing pipeline. FOSS: no SQS, returns None.
"""

from typing import Any


class _NoopSQSProducer:
    def send(self, *args, **kwargs) -> None:
        return None

    def send_batch(self, *args, **kwargs) -> None:
        return None


def get_sqs_producer(*args, **kwargs) -> Any:
    """Returns a no-op SQS producer. Real upstream returns boto3 SQS client wrapper."""
    return _NoopSQSProducer()
