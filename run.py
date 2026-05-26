import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from backend.main import run

if __name__ == "__main__":
    run()
