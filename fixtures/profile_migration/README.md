# Profile Migration Fixture

Repair loading of persisted v1 profile snapshots. v1 uses `name`, while v2 uses
`display_name`; the loader must return the v2 contract and avoid sharing a
mutable default settings object between loaded profiles.
