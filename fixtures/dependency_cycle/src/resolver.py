"""Compute dependency-first install order."""

from src.errors import DependencyCycleError


def dependency_order(graph, root):
    order = []
    completed = set()

    def visit(node):
        if node in completed:
            return
        # BUG: a node being visited is not tracked, so cycles recurse forever.
        for dependency in graph.get(node, []):
            visit(dependency)
        completed.add(node)
        order.append(node)

    visit(root)
    return order
