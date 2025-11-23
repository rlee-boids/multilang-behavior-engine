from app.db.base import Base  # noqa: F401

# Import models so metadata is populated
from app.models.behavior import Behavior  # noqa: F401
from app.models.code_knowledge import CodeKnowledge  # noqa: F401
from app.models.behavior_contract import BehaviorContract  # noqa: F401
from app.models.behavior_implementation import BehaviorImplementation  # noqa: F401
from app.models.behavior_test_run import BehaviorTestRun  # noqa: F401
