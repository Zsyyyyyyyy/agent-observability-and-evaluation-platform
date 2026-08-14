import unittest

from src.errors import DependencyCycleError
from src.resolver import dependency_order


class DependencyOrderTests(unittest.TestCase):
    def test_returns_dependencies_before_the_root(self):
        graph = {"api": ["service", "logging"], "service": ["storage"], "logging": [], "storage": []}
        self.assertEqual(dependency_order(graph, "api"), ["storage", "service", "logging", "api"])

    def test_shared_dependencies_are_not_repeated(self):
        graph = {"app": ["left", "right"], "left": ["common"], "right": ["common"], "common": []}
        self.assertEqual(dependency_order(graph, "app"), ["common", "left", "right", "app"])

    def test_direct_cycle_raises_domain_error(self):
        with self.assertRaises(DependencyCycleError):
            dependency_order({"api": ["worker"], "worker": ["api"]}, "api")


if __name__ == "__main__":
    unittest.main()
