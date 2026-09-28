"""Probe every configured source and print which ones work right now.

    python -m pipeline.check_sources [--hours 48]
"""
import argparse, datetime as dt, logging
from .collect import collect


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=48)
    a = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)
    now = dt.datetime.now(dt.timezone.utc)
    _, st = collect(now - dt.timedelta(hours=a.hours), now)
    for s in sorted(st, key=lambda s: (not s["ok"], s["id"])):
        print(f"{'OK ' if s['ok'] else 'ERR'} {s['count']:4d} in window  {s['secs']:5.1f}s  {s['id']:22s} {s['error'] or ''}")
    print(f"\n{sum(s['ok'] for s in st)}/{len(st)} sources reachable; "
          f"{sum(1 for s in st if s['ok'] and s['count'])} returned items in the last {a.hours:g}h")


if __name__ == "__main__":
    main()
