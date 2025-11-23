from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.models.behavior_implementation import ImplementationStatus


class BehaviorImplementationBase(BaseModel):
    behavior_id: int
    language: str
    repo_url: Optional[str] = None
    revision: Optional[str] = None
    file_path: Optional[str] = None
    status: ImplementationStatus = ImplementationStatus.source
    notes: Optional[str] = None


class BehaviorImplementationCreate(BehaviorImplementationBase):
    pass


class BehaviorImplementationUpdate(BaseModel):
    status: Optional[ImplementationStatus] = None
    notes: Optional[str] = None


class BehaviorImplementationRead(BehaviorImplementationBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
