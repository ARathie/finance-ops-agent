"""Download the GreenMail standalone jar for the mail server tests.

    uv run python tests/contract/get_greenmail.py

It goes to tests/.cache/ (ignored by git). CI runs the same download and
caches the result. Needs Java 17 or newer on the PATH to be useful.
"""

import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from tests.contract.conftest import CACHED_JAR, GREENMAIL_URL  # noqa: E402


def main() -> int:
    if CACHED_JAR.exists():
        print(f"already there: {CACHED_JAR}")
        return 0
    CACHED_JAR.parent.mkdir(parents=True, exist_ok=True)
    print(f"downloading {GREENMAIL_URL}")
    with urllib.request.urlopen(GREENMAIL_URL, timeout=120) as response:  # noqa: S310
        CACHED_JAR.write_bytes(response.read())
    print(f"saved {CACHED_JAR} ({CACHED_JAR.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
