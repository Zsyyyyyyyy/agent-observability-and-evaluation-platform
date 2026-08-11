# Core Repair Suite v1

This suite contains eight deterministic Python code-repair Cases. They increase in task shape rather than code size: empty/null input, parsing default, normalization, bound handling, ordered de-duplication, configuration merge, and cross-file reasoning.

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

All Cases require a correct implementation-only diff and passing tests. Negative security/timeout drills remain unit/integration tests until the manifest gains explicit expected-failure semantics.
