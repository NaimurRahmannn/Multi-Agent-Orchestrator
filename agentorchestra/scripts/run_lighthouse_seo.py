#!/usr/bin/env python

from pathlib import Path
import sys

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agentorchestra.scripts.run_lighthouse_seo import main

if __name__ == "__main__":
    raise SystemExit(main())
