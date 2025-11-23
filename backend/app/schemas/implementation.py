# backend/app/schemas/implementation.py

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class BehaviorImplementationBase(BaseModel):
    behavior_id: int
    language: str
    repo_url: Optional[str] = None
    revision: Optional[str] = None
    file_path: Optional[str] = None
    status: str
    notes: Optional[str] = None


class BehaviorImplementationCreate(BehaviorImplementationBase):
    pass


class BehaviorImplementationUpdate(BaseModel):
    language: Optional[str] = None
    repo_url: Optional[str] = None
    revision: Optional[str] = None
    file_path: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None


class BehaviorImplementationRead(BehaviorImplementationBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True  # pydantic v2 equivalent of orm_mode
