"""Report empty pytest scopes explicitly and preserve every other pytest exit status."""

import sys

import pytest


def main() -> int:
    result = pytest.main(sys.argv[1:])
    if result == pytest.ExitCode.NO_TESTS_COLLECTED:
        print("test infrastructure ready; no tests")
        return 0
    return result


if __name__ == "__main__":
    raise SystemExit(main())
