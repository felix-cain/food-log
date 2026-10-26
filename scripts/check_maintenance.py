"""Exit 1 if anything in maintenance.json is due within its warning window.

Run weekly by .github/workflows/weekly.yml; run by hand with:
    python scripts/check_maintenance.py
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.maintenance import describe, due  # noqa: E402


def main() -> int:
    items = due(date.today())
    if not items:
        print("Nothing due.")
        return 0
    for item in items:
        print(f"- {describe(item)}\n  {item['action']}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
