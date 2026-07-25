"""Budget guard for unattended runs.

config/budget.yaml has carried a max_total_usd since Phase 1 but nothing ever
read it. That was survivable while every run was hand-launched and short; it
isn't once the Optimizer runs unattended for dozens of generations, each firing
off tens of API calls. The OpenRouter key reports `limit: null` -- it's
pay-as-you-go with no ceiling of its own -- so there is no backstop other than
this one.

Spend is read from OpenRouter's own /api/v1/key endpoint rather than summed from
per-call usage fields, because it counts everything charged to the key including
calls made from inside the sandbox, which this process never sees.

Usage:
    from sealed.cost import check_budget, total_spend_usd
    check_budget("before generation 12")   # raises BudgetExceeded past the cap
"""

import requests

from sealed._config import BUDGET, OPENROUTER_API_KEY

KEY_ENDPOINT = "https://openrouter.ai/api/v1/key"


class BudgetExceeded(RuntimeError):
    pass


def total_spend_usd() -> float | None:
    """Cumulative USD charged to this key, or None if the endpoint is unreachable."""
    try:
        resp = requests.get(KEY_ENDPOINT, headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"}, timeout=15)
        resp.raise_for_status()
        return float(resp.json()["data"]["usage"])
    except Exception:
        return None


def check_budget(label: str = "", cap: float | None = None) -> float | None:
    """Raise BudgetExceeded if spend is past the cap. Returns current spend.

    A None spend (endpoint down, network blip) is NOT treated as an overrun --
    halting a long run because a status endpoint hiccuped would be worse than
    the small overshoot risk of continuing. The cap is a guard against a runaway
    loop, not an accounting system.
    """
    cap = cap if cap is not None else BUDGET["max_total_usd"]
    spend = total_spend_usd()
    if spend is None:
        print(f"[budget] could not read spend{' — ' + label if label else ''}; continuing")
        return None

    remaining = cap - spend
    print(f"[budget] ${spend:.3f} spent of ${cap:.2f} cap (${remaining:.3f} left){' — ' + label if label else ''}")
    if spend >= cap:
        raise BudgetExceeded(f"spend ${spend:.3f} has reached the ${cap:.2f} cap in config/budget.yaml; halting")
    return spend


if __name__ == "__main__":
    check_budget("manual check")
