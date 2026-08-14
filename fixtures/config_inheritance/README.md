# Configuration Inheritance Fixture

Repair a recursive configuration resolver. It must merge parent settings before
child settings, support more than one inheritance level, and reject an unknown
parent instead of returning a partial configuration.
