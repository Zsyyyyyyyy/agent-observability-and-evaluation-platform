# Core Repair Suite v2

This suite contains seventeen deterministic Python code-repair Cases. Version 2
keeps the original quick checks and adds nine medium-complexity tasks that expose
multi-file reasoning, operational boundary handling, compatibility work, and
security policy semantics. Every fixture starts with a failing test suite and
can be executed without network access.

| Case | Capability | Budget |
|---|---|---:|
| smoke_calculator_empty_input | empty input | 24 calls / 30k tokens |
| normalize_none_input | null input | 24 calls / 30k tokens |
| parse_port_blank_default | parsing default | 12 calls / 12k tokens |
| safe_slug_punctuation | text normalization | 12 calls / 12k tokens |
| bounded_discount_percent | boundary handling | 12 calls / 12k tokens |
| cross_file_greeting_missing_name | cross-file reasoning | 16 calls / 16k tokens |
| merge_settings_none | configuration merge | 12 calls / 12k tokens |
| deduplicate_tags_ordered | ordered collection repair | 12 calls / 12k tokens |
| config_inheritance_precedence | multi-level configuration inheritance | 18 calls / 18k tokens |
| dependency_cycle_detection | graph traversal and domain errors | 18 calls / 18k tokens |
| batch_partial_failure_isolation | partial failure containment | 18 calls / 18k tokens |
| cache_expiry_boundary | time boundary semantics | 16 calls / 16k tokens |
| profile_v1_migration | backward-compatible state migration | 18 calls / 18k tokens |
| permission_deny_precedence | deny-first authorization policy | 16 calls / 16k tokens |
| inventory_reservation_atomicity | transactional mutation and aggregate validation | 20 calls / 20k tokens |
| cursor_revision_integrity | revision-aware pagination and boundary validation | 18 calls / 18k tokens |
| webhook_signature_canonicalization | canonical serialization and fail-closed verification | 18 calls / 18k tokens |

All Cases require a correct implementation-only diff and passing tests. The v2
batch deliberately has no real-model results yet: it is a reproducible
Benchmark asset, not evidence of Agent quality. Negative security/timeout
drills remain unit/integration tests until the manifest gains explicit
expected-failure semantics.
