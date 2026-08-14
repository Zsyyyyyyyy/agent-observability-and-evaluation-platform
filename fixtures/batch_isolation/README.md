# Batch Failure Isolation Fixture

Repair batch processing so one invalid input is recorded as a per-row rejection
without losing valid rows before or after it. Rejection indexes must refer to
the original input order.
