"""Simulated clock and job execution worker (Person 1)."""

from datetime import datetime, timedelta


def advance_demo_clock(current: datetime, hours: float) -> datetime:
    """Advance simulated time — wired to POST /api/demo/advance later."""
    raise NotImplementedError("Demo clock advance is not implemented.")


def tick_job_execution(jobs: list, current_time: datetime) -> list:
    """Progress running jobs toward completion — not implemented in scaffold."""
    raise NotImplementedError("Demo worker execution tick is not implemented.")


def planned_advance_delta() -> timedelta:
    return timedelta(hours=1)