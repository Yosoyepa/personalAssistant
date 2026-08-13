from __future__ import annotations

import json
from collections.abc import Sequence

from tools.wct.model import GateResult


def text_report(results: Sequence[GateResult], *, quiet: bool = False) -> str:
    if quiet:
        return "\n".join(f"{r.gate_id} {r.status}: {r.summary}" for r in results if r.blocking)
    width = max((len(r.gate_id) for r in results), default=4)
    lines = [f"{'GATE':<{width}}  STATUS  MS      SUMMARY"]
    for result in results:
        lines.append(
            f"{result.gate_id:<{width}}  {result.status:<6}  "
            f"{result.duration_ms:<6}  {result.summary}"
        )
    passed = sum(not result.blocking for result in results)
    lines.append(f"\n{passed}/{len(results)} gates no bloqueantes")
    return "\n".join(lines)


def json_report(results: Sequence[GateResult]) -> str:
    return json.dumps([result.as_dict() for result in results], indent=2, ensure_ascii=False)
