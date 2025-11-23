from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class BehaviorContractBase(BaseModel):
    behavior_id: int
    name: str
    description: Optional[str] = None
    version: str = "1.0.0"
    input_schema: Optional[dict] = None
    output_schema: Optional[dict] = None
    test_cases: Optional[dict] = None  # {"cases": [...]}


class BehaviorContractCreate(BehaviorContractBase):
    pass


class BehaviorContractUpdate(BaseModel):
    description: Optional[str] = None
    version: Optional[str] = None
    input_schema: Optional[dict] = None
    output_schema: Optional[dict] = None
    test_cases: Optional[dict] = None


class BehaviorContractRead(BehaviorContractBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
