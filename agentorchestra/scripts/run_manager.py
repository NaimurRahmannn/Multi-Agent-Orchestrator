#!/usr/bin/env python
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


if __name__ == "__main__":
    from agentorchestra.scripts.run_manager import main

    raise SystemExit(main())
