#!/usr/bin/env python3
"""Print a compact, read-only Agent evolution history from a local Catalog."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from regression_lab.evolution_catalog import EvolutionCatalog


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--agent-id")
    args = parser.parse_args()
    try:
        history = EvolutionCatalog(args.catalog).history(args.agent_id)
    except ValueError as exc:
        print(f"EVOLUTION CATALOG ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(history, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
