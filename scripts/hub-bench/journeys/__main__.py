"""``python -m journeys <command>`` from ``scripts/hub-bench``.

    measure --hub x1s [--repeat 2] [--full]   layer costs, read stability
    run <journey> --hub x1s                   a hand-written journey (hand/<journey>.py)
"""

from __future__ import annotations

import argparse
import importlib
import sys


def main() -> int:
    parser = argparse.ArgumentParser(prog="journeys")
    sub = parser.add_subparsers(dest="command", required=True)
    m = sub.add_parser("measure")
    m.add_argument("--hub", required=True)
    m.add_argument("--repeat", type=int, default=2)
    m.add_argument("--full", action="store_true")
    r = sub.add_parser("run")
    r.add_argument("journey")
    r.add_argument("--hub", required=True)
    r.add_argument("--no-ui", action="store_true", help="skip the passive browser tabs")
    r.add_argument("--headed", action="store_true", help="show the passive tabs")
    args = parser.parse_args()

    if args.command == "measure":
        from .measure import measure
        return measure(args.hub, repeat=args.repeat, full=args.full)
    from .runner import Runner
    module = importlib.import_module(f".hand.{args.journey}", __package__)
    if args.hub.strip().upper() == "X2" and not getattr(module, "X2_ALLOWED", False):
        # Plan section 7.2: the X2 is production; only journeys built for it
        # (sandbox-only, mandatory clean exit) may run there.
        sys.exit(f"{args.journey} is not marked X2_ALLOWED (plan section 7.2)")
    return Runner(args.journey, args.hub, ui=not args.no_ui, headed=args.headed,
                  actor=bool(getattr(module, "NEEDS_ACTOR", False))).run(module.journey)


if __name__ == "__main__":
    sys.exit(main())
