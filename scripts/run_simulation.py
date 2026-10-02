"""CLI entry. The runner lives in src.sim so the eval does not import a root script."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.sim.runner import main

if __name__ == "__main__":
    main()
