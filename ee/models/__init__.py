# Re-exports for `from ee.models import X` convenience imports used in OSS code.
# Production paths only -- test-only imports (e.g. EnterprisePropertyDefinition
# in posthog/models/test/test_tagged_item_model.py) are also re-exported
# defensively so test files don't crash even though they're not loaded by the
# production containers.
#
# Not re-exported: EnterprisePropertyDefinition (raises ImportError -- see
# ee/models/property_definition.py for why).
from ee.models.conversation import Conversation
from ee.models.dashboard_privilege import DashboardPrivilege
from ee.models.event_definition import EnterpriseEventDefinition
from ee.models.rbac.access_control import AccessControl
from ee.models.rbac.role import Role, RoleMembership

__all__ = [
    "AccessControl",
    "Conversation",
    "DashboardPrivilege",
    "EnterpriseEventDefinition",
    "Role",
    "RoleMembership",
]
