import sys
from pathlib import Path

# The tooling is a flat directory of scripts, not a package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
