from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
import enum


class TestRunStatus(str, enum.Enum):
    passed = "passed"
    failed = "failed"
    error = "error"


class BehaviorTestRun(Base):
    __tablename__ = "behavior_test_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    behavior_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("behaviors.id", ondelete="CASCADE"), index=True
    )
    language: Mapped[str] = mapped_column(String(64), nullable=False)
    contract_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("behavior_contracts.id", ondelete="CASCADE"), index=True
    )
    implementation_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("behavior_implementations.id", ondelete="SET NULL"), nullable=True
    )

    status: Mapped[TestRunStatus] = mapped_column(
        Enum(TestRunStatus, name="testrun_status_enum"),
        nullable=False,
    )
    details: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    behavior: Mapped["Behavior"] = relationship(
        "Behavior",
        back_populates="test_runs",
    )
    contract: Mapped["BehaviorContract"] = relationship(
        "BehaviorContract",
        back_populates="test_runs",
    )
    implementation: Mapped["BehaviorImplementation"] = relationship(
        "BehaviorImplementation",
        back_populates="test_runs",
    )
