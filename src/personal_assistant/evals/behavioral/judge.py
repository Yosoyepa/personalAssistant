"""Ask an LLM whether one reminder extraction is faithful to the user's text.

The judge grades; it never repairs. It receives the user text and whatever the
runtime extracted (or `None` when the runtime declined) and returns a binary
verdict with a confidence.

Two refusals are deliberate and both are enforced here rather than in prose:

* **A malformed verdict is a sanitized failure, never a default PASS.** A judge
  whose parse errors resolve to "acceptable" would report agreement it never
  reached, which is worse than no judge at all.
* **The judge has no authority over the release gate.** It produces a
  `JudgeVerdict`; deciding what that means is the caller's problem, and
  `docs/adr/ADR-006-behavioral-eval-tier-and-judge.md` fixes the pre-registered
  thresholds it must clear before anyone treats it as blocking.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import Field, ValidationError

from personal_assistant.application.dto.context import TokenBudget
from personal_assistant.application.dto.runtime import LLMRequest
from personal_assistant.application.ports.prompts import PromptCatalogPort
from personal_assistant.application.ports.services import LLMProvider
from personal_assistant.evals.schema import StrictModel

JUDGE_REMINDER_EXTRACTION_PROMPT_ID = "judge_reminder_extraction"
JUDGE_SCHEMA_NAME = "judge_reminder_extraction"
JUDGE_MAX_TOKENS = 256
JUDGE_TEMPERATURE = 0.0
JUDGE_TOKEN_LIMIT = 1_000

Verdict = Literal["pass", "fail"]


class JudgeResponse(StrictModel):
    """The exact shape `prompts/judge_reminder_extraction/v1.md` promises."""

    verdict: Verdict
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1, max_length=300)


@dataclass(frozen=True, slots=True)
class JudgeVerdict:
    """A graded verdict, or the sanitized record of a judge that failed.

    ``accepted`` is the graded answer and is ``False`` whenever the judge could
    not produce a well-formed verdict. ``error`` distinguishes "the judge said
    fail" from "the judge broke", which the calibration report has to separate:
    counting broken calls as genuine FAILs would flatter the true negative rate.
    """

    label_id: str
    accepted: bool
    confidence: float
    reason: str
    error: str | None = None

    @property
    def usable(self) -> bool:
        return self.error is None


def _sanitize(exc: Exception) -> str:
    """Name the failure without carrying any provider-supplied text.

    Deliberately drops `str(exc)`. A provider that echoes the request in its
    error message would otherwise copy the user's text into
    `docs/development/judge-calibration-v1.md`, which is committed. Truncating
    is not enough — the text sits at the front of such messages.

    The cost is diagnosability during a live recording pass. The mitigation is
    that the verdict still carries `label_id`, so a single case can be re-run
    with `--mode live` to see the raw error outside the report.
    """
    return exc.__class__.__name__


def render_judge_prompt(
    *,
    text: str,
    extraction: dict[str, object] | None,
    now: datetime,
    timezone: str,
    prompt_catalog: PromptCatalogPort,
) -> str:
    """Render the versioned judge prompt from trusted application variables.

    `text` and `extraction` are untrusted, so both go through `repr`/`json`
    exactly the way `_render_intent_prompt` treats user text: the template
    substitutes them as data, never as further instructions.
    """
    return prompt_catalog.render(
        JUDGE_REMINDER_EXTRACTION_PROMPT_ID,
        {
            "now": now.isoformat(),
            "timezone": timezone,
            "text": repr(text),
            "extraction": json.dumps(extraction, ensure_ascii=False, sort_keys=True),
        },
    ).text


def judge_extraction(
    *,
    label_id: str,
    text: str,
    extraction: dict[str, object] | None,
    now: datetime,
    timezone: str,
    llm: LLMProvider,
    prompt_catalog: PromptCatalogPort,
) -> JudgeVerdict:
    """Grade one extraction. Any failure resolves to a non-accepted verdict."""
    try:
        prompt = render_judge_prompt(
            text=text,
            extraction=extraction,
            now=now,
            timezone=timezone,
            prompt_catalog=prompt_catalog,
        )
    except (KeyError, ValueError) as exc:
        return JudgeVerdict(
            label_id=label_id,
            accepted=False,
            confidence=0.0,
            reason="judge prompt could not be rendered",
            error=_sanitize(exc),
        )

    try:
        result = llm.complete(
            LLMRequest(
                schema_name=JUDGE_SCHEMA_NAME,
                max_tokens=JUDGE_MAX_TOKENS,
                temperature=JUDGE_TEMPERATURE,
                prompt=prompt,
            ),
            budget=TokenBudget(limit=JUDGE_TOKEN_LIMIT),
        )
    except Exception as exc:
        # Broad on purpose: a provider timeout, a transport error, and a quota
        # rejection must all resolve to a non-accepted verdict. Letting one
        # escape would abort the run mid-corpus and lose the graded results.
        return JudgeVerdict(
            label_id=label_id,
            accepted=False,
            confidence=0.0,
            reason="judge call failed",
            error=_sanitize(exc),
        )

    try:
        response = JudgeResponse.model_validate(result.data)
    except ValidationError as exc:
        return JudgeVerdict(
            label_id=label_id,
            accepted=False,
            confidence=0.0,
            reason="judge returned a malformed verdict",
            error=f"ValidationError: {exc.error_count()} schema errors",
        )

    return JudgeVerdict(
        label_id=label_id,
        accepted=response.verdict == "pass",
        confidence=response.confidence,
        reason=response.reason,
    )
