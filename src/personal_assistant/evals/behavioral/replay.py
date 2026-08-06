"""Record and replay `LLMProvider` calls so behavioral runs work offline.

`LLMProvider` is a single-method Protocol and `LLMResult` is a closed model, so
a cassette entry is a dumped `LLMResult` keyed by the request that produced it.

A missing cassette entry in replay mode is a hard, sanitized failure. It is
never a skip and never an empty pass — the same policy the L1 gate applies to a
missing `TEST_POSTGRES_DSN`, and for the same reason: a gate that quietly
degrades to zero assertions reports success for work it did not do.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import ValidationError

from personal_assistant.application.dto.context import TokenBudget
from personal_assistant.application.dto.runtime import LLMRequest, LLMResult
from personal_assistant.application.ports.services import LLMProvider
from personal_assistant.evals.behavioral.schema import (
    Cassette,
    CassetteEntry,
    Provenance,
)


class CassetteError(RuntimeError):
    """The cassette cannot satisfy a request that replay mode requires."""


def request_key(request: LLMRequest) -> str:
    """Derive a stable cassette key from every field that shapes a response.

    `LLMRequest` carries no model name — the adapter picks the model — so the
    key covers the request only. The provider and model that answered are
    recorded in the entry, which is what lets a later run detect that the
    provider swapped models underneath a committed cassette.
    """
    payload = json.dumps(
        {
            "schema_name": request.schema_name,
            "prompt": request.prompt,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def read_cassette(path: Path) -> Cassette:
    """Load and validate a cassette, including its provenance claim."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CassetteError(f"cannot read valid JSON cassette from {path}") from exc
    try:
        return Cassette.model_validate(raw)
    except ValidationError as exc:
        raise CassetteError(
            f"invalid cassette {path} ({exc.error_count()} schema errors)"
        ) from exc


def load_cassette(path: Path) -> dict[str, CassetteEntry]:
    return {entry.key: entry for entry in read_cassette(path).entries}


class ReplayLLMProvider:
    """Serve recorded responses; refuse anything the cassette does not hold."""

    def __init__(self, entries: dict[str, CassetteEntry]) -> None:
        self._entries = dict(entries)
        self._served: list[str] = []

    @property
    def served_keys(self) -> tuple[str, ...]:
        return tuple(self._served)

    def complete(self, request: LLMRequest, *, budget: TokenBudget) -> LLMResult:
        key = request_key(request)
        entry = self._entries.get(key)
        if entry is None:
            raise CassetteError(
                f"no cassette entry for schema {request.schema_name!r} (key {key[:12]}); "
                "re-record the cassette in --mode record"
            )
        self._served.append(key)
        return LLMResult(
            provider=entry.provider,
            model=entry.model,
            data=dict(entry.data),
            input_tokens=entry.inputTokens,
            output_tokens=entry.outputTokens,
        )


class RecordingLLMProvider:
    """Delegate to a real provider and accumulate cassette entries."""

    def __init__(self, inner: LLMProvider) -> None:
        self._inner = inner
        self._entries: dict[str, CassetteEntry] = {}

    @property
    def entries(self) -> tuple[CassetteEntry, ...]:
        return tuple(self._entries[key] for key in sorted(self._entries))

    def complete(self, request: LLMRequest, *, budget: TokenBudget) -> LLMResult:
        result = self._inner.complete(request, budget=budget)
        key = request_key(request)
        self._entries[key] = CassetteEntry(
            key=key,
            schemaName=request.schema_name,
            provider=result.provider,
            model=result.model,
            data=dict(result.data),
            inputTokens=result.input_tokens,
            outputTokens=result.output_tokens,
        )
        return result

    def as_cassette(
        self, *, recorded_at: str, provenance: Provenance = "recorded"
    ) -> Cassette:
        if not self._entries:
            raise CassetteError("refusing to write an empty cassette")
        return Cassette(
            schemaVersion=1,
            provenance=provenance,
            recordedAt=recorded_at,
            entries=list(self.entries),
        )


def write_cassette(path: Path, cassette: Cassette) -> None:
    """Write a cassette with LF endings and a single trailing newline."""
    payload = json.dumps(
        cassette.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    path.write_text(f"{payload}\n", encoding="utf-8", newline="\n")
