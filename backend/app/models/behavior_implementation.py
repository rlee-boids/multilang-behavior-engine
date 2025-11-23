from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
import enum


class ImplementationStatus(str, enum.Enum):
    source = "source"
    converted = "converted"
    candidate = "candidate"
    validated = "validated"
    error = "error"


class BehaviorImplementation(Base):
    __tablename__ = "behavior_implementations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    behavior_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("behaviors.id", ondelete="CASCADE"), index=True
    )

    language: Mapped[str] = mapped_column(String(64), index=True)
    repo_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    revision: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    file_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    status: Mapped[ImplementationStatus] = mapped_column(
        Enum(ImplementationStatus, name="implementation_status_enum"),
        default=ImplementationStatus.source,
    )

    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    behavior: Mapped["Behavior"] = relationship(
        "Behavior",
        back_populates="implementations",
    )
    test_runs: Mapped[List["BehaviorTestRun"]] = relationship(
        "BehaviorTestRun",
        back_populates="implementation",
        cascade="all, delete-orphan",
    )
