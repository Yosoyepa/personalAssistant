# Red-team corpus

`cases.yaml` contains two adversarial probes for every failure mode F1–F15.
Run `uv run wct selftest redteam`. The command fails if a gate disappears,
a checker stops rejecting its payload, or any failure mode has fewer than two cases.

