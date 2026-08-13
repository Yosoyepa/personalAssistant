"""Level-2 behavioral evaluation tier: human labels, replayed LLM calls.

This package is deliberately separate from the Level-1 deterministic gate in
`personal_assistant.evals`. The L1 runner asserts exact equality against a
closed expected model and forbids LLM judges (see `eval/README.md`); this tier
scores non-deterministic behavior against a human-labeled corpus and reports
calibrated rates rather than a single pass/fail per case.

Nothing here runs during the L1 release gate.
"""
