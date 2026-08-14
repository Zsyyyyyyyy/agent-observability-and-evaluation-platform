# Dependency Cycle Fixture

Repair dependency ordering without changing its deterministic post-order
behaviour. A direct or indirect cycle must raise `DependencyCycleError`, rather
than leaking recursion details or returning a partial order.
