# Smoke Calculator Fixture

This fixture intentionally starts with a failing implementation: `calculate("")`
raises `ValueError`. The Replay model edits only `src/calculator.py`; the test
suite then verifies the empty-input fix and the original non-empty behavior.

