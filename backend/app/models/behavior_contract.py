from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class BehaviorContract(Base):
    __tablename__ = "behavior_contracts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    behavior_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("behaviors.id", ondelete="CASCADE"), index=True
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    version: Mapped[str] = mapped_column(String(64), default="1.0.0")

    input_schema: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    output_schema: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    test_cases: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # {"cases": [...]}

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    behavior: Mapped["Behavior"] = relationship(
        "Behavior",
        back_populates="contracts",
    )
    test_runs: Mapped[List["BehaviorTestRun"]] = relationship(
        "BehaviorTestRun",
        back_populates="contract",
        cascade="all, delete-orphan",
    )
