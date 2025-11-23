from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class CodeKnowledge(Base):
    """
    Knowledge base entries populated by AI analyzer.
    One row per analyzed file or symbol.
    """

    __tablename__ = "code_knowledge"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    repo_url: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)
    revision: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)
    file_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    language: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    behavior_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("behaviors.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )

    symbol_name: Mapped[Optional[str]] = mapped_column(String(255), index=True, nullable=True)
    symbol_kind: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_excerpt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    io_description: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    dependencies: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    short_summary: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    details_md: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    tags: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    audience: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    domain: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    analyzer_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    analyzer_version: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    model_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    last_edited_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    is_human_reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)

    behavior: Mapped["Behavior"] = relationship(
        "Behavior",
        back_populates="code_knowledge_entries",
    )
