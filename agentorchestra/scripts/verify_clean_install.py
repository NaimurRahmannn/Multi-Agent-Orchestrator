#!/usr/bin/env python

import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agentorchestra.scripts.verify_clean_install import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
