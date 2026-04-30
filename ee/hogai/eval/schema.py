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


# Snapshot types imported by products/posthog_ai/dags/snapshot_team_data.py.
# Real upstream uses Pydantic BaseModel; here plain dataclasses are enough
# because Dagster doesn't run on FOSS and these classes are never instantiated.
@dataclass
class BaseSnapshot:
    team_id: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ClickhouseTeamDataSnapshot(BaseSnapshot):
    pass


@dataclass
class DataWarehouseTableSnapshot(BaseSnapshot):
    pass


@dataclass
class GroupTypeMappingSnapshot(BaseSnapshot):
    pass


@dataclass
class PropertyDefinitionSnapshot(BaseSnapshot):
    pass


@dataclass
class PropertyTaxonomySnapshot(BaseSnapshot):
    pass


@dataclass
class ActorsPropertyTaxonomySnapshot(BaseSnapshot):
    pass


@dataclass
class TeamTaxonomyItemSnapshot(BaseSnapshot):
    pass
