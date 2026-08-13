from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class Status(StrEnum):
    """Possible outcomes from a quality gate."""

    PASS = "PASS"
    FAIL = "FAIL"
    SKIP = "SKIP"
    ERROR = "ERROR"


@dataclass(frozen=True)
class GateResult:
    """Serializable outcome of one named gate."""

    gate_id: str
    status: Status
    summary: str
    duration_ms: int = 0
    details: list[str] = field(default_factory=list)
    command: str | None = None

    @property
    def blocking(self) -> bool:
        return self.status in {Status.FAIL, Status.ERROR}

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
