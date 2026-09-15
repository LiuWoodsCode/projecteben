#!/usr/bin/env python3

from __future__ import annotations

import sys
import time

import solid_color_display as scd


DURATION = 5.0
PINK = (255, 0, 170)


def main() -> int:
    try:
        scd.display_color(*PINK)
        print(f"Pink active for {DURATION:g} seconds.")

        deadline = time.monotonic() + DURATION
        while (remaining := deadline - time.monotonic()) > 0:
            time.sleep(min(remaining, 0.1))

    except KeyboardInterrupt:
        print("\nInterrupted; restoring...")
        return 130
    except Exception as exc:
        print(f"fatal: {exc}", file=sys.stderr)
        return 1
    finally:
        scd.return_to_desktop()

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
