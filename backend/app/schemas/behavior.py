from datetime import datetime
from typing import Optional

from pydantic import BaseModel, field_validator


class BehaviorBase(BaseModel):
    name: str
    description: Optional[str] = None
    domain: Optional[str] = None
    tags: Optional[list[str]] = None


class BehaviorCreate(BehaviorBase):
    pass


class BehaviorUpdate(BaseModel):
    description: Optional[str] = None
    domain: Optional[str] = None
    tags: Optional[list[str]] = None


class BehaviorRead(BehaviorBase):
    id: int
    created_at: datetime
    updated_at: datetime

    # Normalize legacy shapes for tags
    @field_validator("tags", mode="before")
    @classmethod
    def normalize_tags(cls, v):
        # New rows: already a list -> pass through
        if v is None:
            return None
        if isinstance(v, list):
            return v
        # Old rows: {"tags": [...]}
        if isinstance(v, dict) and "tags" in v:
            return v["tags"]
        # In case some row had tags stored as a JSON string
        if isinstance(v, str):
            try:
                import json

                loaded = json.loads(v)
                if isinstance(loaded, list):
                    return loaded
            except Exception:
                # Fall back to treating the string as a single tag
                return [v]
        return v

    class Config:
        from_attributes = True
