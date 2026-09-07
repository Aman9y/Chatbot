"""Shared model building blocks."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum as PyEnum
from typing import Any

from sqlalchemy import DateTime, Enum, Uuid
from sqlalchemy.orm import Mapped, mapped_column


def utcnow() -> datetime:
    """Naive UTC 'now'.

    We deliberately store naive UTC everywhere so behaviour is identical on
    Postgres and SQLite (SQLite has no tz-aware storage). Meta unix timestamps
    are converted the same way in :mod:`app.services.timeutils`.
    """

    return datetime.now(UTC).replace(tzinfo=None)


def enum_column(enum_cls: type[PyEnum], **kwargs: Any) -> Mapped[Any]:
    """A portable string-backed enum column that persists the enum *value*."""

    return mapped_column(
        Enum(
            enum_cls,
            native_enum=False,
            length=48,
            values_callable=lambda e: [member.value for member in e],
            validate_strings=True,
        ),
        **kwargs,
    )


class UUIDMixin:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
