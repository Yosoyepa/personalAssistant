from __future__ import annotations

import unittest

from personal_assistant.evals.executors.egress_allowlist_v1 import (
    InputModel,
    execute,
)


class EgressAllowlistExecutorTests(unittest.TestCase):
    def test_llm_allowlisted_scenario_completes_one_request(self) -> None:
        result = execute(InputModel(scenario="llm-allowlisted"))
        self.assertEqual(
            result,
            {"outcome": "allowed", "code": None, "urlopenCalls": 1},
        )

    def test_llm_not_covered_scenario_blocks_before_connection(self) -> None:
        result = execute(InputModel(scenario="llm-not-covered"))
        self.assertEqual(
            result,
            {"outcome": "blocked", "code": "guardrail_blocked", "urlopenCalls": 0},
        )

    def test_telegram_not_covered_scenario_blocks_before_connection(self) -> None:
        result = execute(InputModel(scenario="telegram-not-covered"))
        self.assertEqual(
            result,
            {"outcome": "blocked", "code": "guardrail_blocked", "urlopenCalls": 0},
        )

    def test_startup_not_covered_scenario_rejects_settings(self) -> None:
        result = execute(InputModel(scenario="startup-not-covered"))
        self.assertEqual(
            result,
            {"outcome": "startup_rejected", "code": None, "urlopenCalls": 0},
        )


if __name__ == "__main__":
    unittest.main()
