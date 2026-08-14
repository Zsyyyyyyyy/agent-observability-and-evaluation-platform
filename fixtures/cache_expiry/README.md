# Cache Expiry Fixture

Repair cache reads with an injected clock. Entries are usable strictly before
their expiry timestamp; an exact expiry boundary is a cache miss.
