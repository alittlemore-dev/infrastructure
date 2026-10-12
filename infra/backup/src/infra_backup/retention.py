"""Three references to distinct immutable sets; weeks are ISO weeks in UTC."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class RetentionRefs:
    latest: str | None = None
    weekly_current: str | None = None
    weekly_previous: str | None = None
    weekly_key: str | None = None


def advance_retention(refs: RetentionRefs, run_id: str, completed_at: datetime) -> RetentionRefs:
    if completed_at.tzinfo is None:
        raise ValueError("timezone-aware completion time is required")
    iso = completed_at.astimezone(timezone.utc).isocalendar()
    week = f"{iso.year:04d}-W{iso.week:02d}"
    if refs.weekly_key is not None and week < refs.weekly_key:
        raise ValueError("completion time predates the retained week")
    if week == refs.weekly_key:
        return RetentionRefs(run_id, refs.weekly_current, refs.weekly_previous, week)
    return RetentionRefs(run_id, run_id, refs.weekly_current, week)


def protected_ids(refs: RetentionRefs) -> set[str]:
    return {value for value in (refs.latest, refs.weekly_current, refs.weekly_previous) if value is not None}
