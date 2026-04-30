"""FOSS stub for ee.hogai.eval.schema.

Used by products/posthog_ai/dags/* (Dagster-loaded only) and posthog/dags/
tests. Self-host doesn't run Dagster, so these are effectively dead code.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DatasetInput:
    name: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvalsDockerImageConfig:
    image: str = ""
    tag: str = "latest"


@dataclass
class TeamEvaluationSnapshot:
    team_id: int = 0
    snapshot_id: str = ""


@dataclass
class PostgresTeamDataSnapshot:
    team_id: int = 0


@dataclass
class TeamSnapshot:
    team_id: int = 0
