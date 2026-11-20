"""SQLAlchemy data models for WebsiteMonitor."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class MonitoredSite(Base):
    """A website or API endpoint being monitored."""

    __tablename__ = "monitored_site"

    STATUS_UNKNOWN = "unknown"        # никогда ещё не проверялся (серый)
    STATUS_AVAILABLE = "available"    # доступен (зелёный)
    STATUS_UNAVAILABLE = "unavailable"  # недоступен (красный)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    interval_seconds: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    current_status: Mapped[str] = mapped_column(
        String(20), default=STATUS_UNKNOWN, nullable=False
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_response_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_checking: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    checks: Mapped[list["CheckHistory"]] = relationship(
        back_populates="site",
        cascade="all, delete-orphan",
        order_by="CheckHistory.checked_at.desc()",
    )
    incidents: Mapped[list["Incident"]] = relationship(
        back_populates="site", cascade="all, delete-orphan"
    )


class CheckHistory(Base):
    """A single availability check result."""

    __tablename__ = "check_history"
    __table_args__ = (
        Index("ix_check_site_time", "site_id", "checked_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[int] = mapped_column(
        ForeignKey("monitored_site.id", ondelete="CASCADE"), nullable=False
    )
    checked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False)
    response_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    site: Mapped[MonitoredSite] = relationship(back_populates="checks")


class Incident(Base):
    """An outage incident: created on first failure, closed on recovery."""

    __tablename__ = "incident"
    __table_args__ = (
        Index("ix_incident_site_resolved", "site_id", "is_resolved"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[int] = mapped_column(
        ForeignKey("monitored_site.id", ondelete="CASCADE"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    site: Mapped[MonitoredSite] = relationship(back_populates="incidents")