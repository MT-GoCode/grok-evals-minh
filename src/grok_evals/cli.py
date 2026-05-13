"""CLI: list / query / run."""
from __future__ import annotations

import argparse
import json
import random
import sys

from . import registry


def cmd_list(_args: argparse.Namespace) -> int:
    for eid in registry.all_ids():
        ev = registry.get(eid)
        print(f"{eid:24s}  area={ev.meta.area:9s}  {ev.meta.description}")
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    ev = registry.get(args.eval_id)
    ids = ev.ids(limit=None if args.ids_all else 20)
    info = {
        "eval_id": args.eval_id,
        "meta": ev.meta.__dict__,
        "total_ids": len(ev.ids(limit=None)),
        "shown_ids": ids,
    }
    print(json.dumps(info, indent=2))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    ev = registry.get(args.eval_id)
    if args.model:
        ev.model = args.model
    if args.ids:
        chosen = [i for i in args.ids.split(",") if i]
    else:
        all_ids = ev.ids(limit=None)
        n = args.n or 3
        rng = random.Random(args.seed)
        chosen = rng.sample(all_ids, min(n, len(all_ids)))
    print(f"[run] eval={args.eval_id} model={ev.model} n={len(chosen)}")
    results = list(ev.run(chosen))
    out = ev.write_jsonl(results, tag=args.tag)
    agg = ev.aggregate(results)
    print(f"[run] wrote {out}")
    print(json.dumps(agg, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="grok-evals")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="list available evals").set_defaults(func=cmd_list)

    q = sub.add_parser("query", help="show metadata + IDs for an eval")
    q.add_argument("eval_id")
    q.add_argument("--ids-all", action="store_true")
    q.set_defaults(func=cmd_query)

    r = sub.add_parser("run", help="run an eval on selected IDs")
    r.add_argument("eval_id")
    r.add_argument("--ids", help="comma-separated task IDs")
    r.add_argument("--n", type=int, default=None, help="if --ids not given, random sample this many")
    r.add_argument("--seed", type=int, default=0)
    r.add_argument("--model", default=None)
    r.add_argument("--tag", default="", help="suffix for output file")
    r.set_defaults(func=cmd_run)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
