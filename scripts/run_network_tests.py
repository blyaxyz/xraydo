#!/usr/bin/env python3
"""Run security-sensitive socket tests and fail CI if any are skipped."""

from __future__ import annotations

import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    discovered = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_*.py")
    selected = unittest.TestSuite()

    def visit(suite: unittest.TestSuite) -> None:
        for test in suite:
            if isinstance(test, unittest.TestSuite):
                visit(test)
            elif ("NetworkControlPlaneTests" in test.id()
                  or "SubscriptionServerTests" in test.id()):
                selected.addTest(test)

    visit(discovered)
    result = unittest.TextTestRunner(verbosity=2).run(selected)
    if result.skipped:
        print("network security tests were skipped; CI runner must provide loopback sockets",
              file=sys.stderr)
        return 1
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
