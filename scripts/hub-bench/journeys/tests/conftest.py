"""The pure modules only: nothing here imports ha_bench (which needs
scripts/.ha-config.json and a live HA).

Run from the repo root: ``.venv-bench/Scripts/python -m pytest scripts/hub-bench/journeys/tests``.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
