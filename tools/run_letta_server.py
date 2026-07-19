from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LETTA_SRC = ROOT / "letta"
if str(LETTA_SRC) not in sys.path:
    sys.path.insert(0, str(LETTA_SRC))

from letta.server.rest_api.app import start_server


if __name__ == "__main__":
    start_server(port=8283, host="127.0.0.1")
