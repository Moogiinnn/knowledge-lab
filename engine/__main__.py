"""Command line: python -m engine synth|run ...

  python -m engine synth --out data/synthetic --students 60 --seed 0
  python -m engine run experiments/synthetic.json --out results/synthetic
"""

import argparse
import json
import sys

from . import synthetic
from .experiment import print_table, run


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python -m engine")
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("synth", help="write a synthetic events.csv + graph.json")
    s.add_argument("--out", required=True)
    s.add_argument("--students", type=int, default=60)
    s.add_argument("--days", type=int, default=42)
    s.add_argument("--seed", type=int, default=0)
    r = sub.add_parser("run", help="run an experiment config")
    r.add_argument("config")
    r.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    if args.command == "synth":
        events, graph = synthetic.write(args.out, n_students=args.students, days=args.days, seed=args.seed)
        print(f"wrote {events} and {graph}")
    else:
        with open(args.config, encoding="utf-8") as fh:
            config = json.load(fh)
        summary = run(config, args.out)
        print(print_table(summary))
        info = summary["run_info"]
        print(f"\nsizes {info['sizes']}  students {info['students']}  dropped {info['dropped']}")
        print(f"results in {args.out}")


if __name__ == "__main__":
    main()
