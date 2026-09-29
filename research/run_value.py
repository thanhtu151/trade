"""Pre-registered value (E/P) experiment on the point-in-time VN universe.

Fundamentals start in 2018-Q1 and TTM earnings need four published quarters,
so the development period starts mid-2019. Trials were fixed before any result
was computed and are appended to the shared trial log (N includes momentum).

    python -m research.fundamentals          # download quarterly fundamentals first
    python -m research.run_value dev
    python -m research.run_value holdout     # one shot
"""

import argparse

from research.fundamentals import earnings_yield_panel, load_fundamentals
from research.run_momentum import HOLDOUT, PLACEBO_RUNS, load_panel, run_experiment
from research.strategies import Value

DEV = ("2019-07-01", "2023-12-31")
PRIMARY = "value_ep"
TRIALS = {
    "value_ep": dict(mode="ep"),
    "value_ep_lowturn": dict(mode="ep_lowturn"),
    "value_ep_mom": dict(mode="ep_mom"),
}


def make_factory(panel):
    earnings, caps = load_fundamentals()
    ep, shares = earnings_yield_panel(panel, earnings, caps)

    def factory(panel_, params, **extra):
        return Value(panel_, ep, shares, **params, **extra)

    return factory


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("period", choices=("dev", "holdout"))
    parser.add_argument("--force-holdout", action="store_true")
    parser.add_argument("--placebo", type=int, default=PLACEBO_RUNS)
    args = parser.parse_args()
    panel = load_panel()
    print(run_experiment("value", args.period, TRIALS, PRIMARY, factory=make_factory(panel), panel=panel,
                         placebo_runs=args.placebo, force_holdout=args.force_holdout, dev=DEV, holdout=HOLDOUT))


if __name__ == "__main__":
    main()
