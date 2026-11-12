"""Rend les modules du projet (rules.py, scoring.py...) importables depuis les tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
