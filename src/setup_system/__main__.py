"""Start the CLI through a standard-library boundary if its runtime is incomplete."""

import re
import sys


def main() -> None:
    try:
        from setup_system.entrypoint import main as launch
    except ImportError as failure:
        module = failure.name if isinstance(failure, ModuleNotFoundError) else None
        safe = module if module and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,127}", module) else None
        message = (
            f"Required Python module {safe} is unavailable."
            if safe
            else "The application Python runtime is incomplete."
        )
        print(f"Error [dependency_missing]: {message}", file=sys.stderr)
        raise SystemExit(1) from None
    launch()


if __name__ == "__main__":
    main()
