from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.models.behavior_test_run import TestRunStatus


class BehaviorTestRunBase(BaseModel):
    behavior_id: int
    language: str
    contract_id: int
    implementation_id: Optional[int] = None
    status: TestRunStatus
    details: Optional[dict] = None


class BehaviorTestRunCreate(BehaviorTestRunBase):
    pass


class BehaviorTestRunRead(BehaviorTestRunBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True
