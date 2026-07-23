"""Run due discovery recipes for a Railway cron service (or local one-shot)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.container import Container


def main() -> None:
    result = Container().discovery_recipe_service.run_due()
    print(
        f"discovery cron complete: due={result['due_count']} "
        f"ran={result['ran_count']} errors={result['error_count']} "
        f"created={result['created_total']}"
    )


if __name__ == "__main__":
    main()
