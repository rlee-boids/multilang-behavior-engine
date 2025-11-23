from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlalchemy import DateTime, Integer, String, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Behavior(Base):
    __tablename__ = "behaviors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    domain: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Store tags as a JSON array of strings, e.g. ["plot", "png", "io"]
    tags: Mapped[Optional[list[str]]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    code_knowledge_entries: Mapped[List["CodeKnowledge"]] = relationship(
        "CodeKnowledge",
        back_populates="behavior",
        cascade="all, delete-orphan",
    )
    contracts: Mapped[List["BehaviorContract"]] = relationship(
        "BehaviorContract",
        back_populates="behavior",
        cascade="all, delete-orphan",
    )
    implementations: Mapped[List["BehaviorImplementation"]] = relationship(
        "BehaviorImplementation",
        back_populates="behavior",
        cascade="all, delete-orphan",
    )
    test_runs: Mapped[List["BehaviorTestRun"]] = relationship(
        "BehaviorTestRun",
        back_populates="behavior",
        cascade="all, delete-orphan",
    )
