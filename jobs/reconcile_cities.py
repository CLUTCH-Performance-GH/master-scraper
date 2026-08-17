"""Final city/state reconciliation across all companies (no network).

A confirmed row must never show a city that doesn't belong to its state (e.g.
'Sioux Falls, Iowa' or an HQ city like 'Inver Grove Heights, Tennessee'). For
each row:
  - city consistent with state            -> keep
  - city unambiguously in ONE target state -> correct the state to the city
  - only one target-state candidate        -> use it (best guess)
  - otherwise (ambiguous / non-target)      -> blank the city, keep the state

Run: PYTHONPATH=. python3 jobs/reconcile_cities.py
"""
import json
from pathlib import Path

from jobs.contacts_lib import (ABBR_STATE, STATE_ABBR, TARGET_ABBR, _best_known_city,
                               load_city_resolver)
from jobs.run_company import COMPANIES

GAZ = "data/us_cities.csv"


def main():
    city2states = load_city_resolver(GAZ)
    fixed_state = blanked = 0
    for key in COMPANIES:
        pf = Path(f"output/contacts/{key}.json")
        if not pf.exists():
            continue
        rows = json.loads(pf.read_text())
        for r in rows:
            city = _best_known_city(r.get("city", ""), city2states)
            if not city:
                if r.get("city"):
                    r["city"] = ""
                    blanked += 1
                continue
            allstates = city2states.get(city.lower(), set())
            tstates = allstates & TARGET_ABBR
            ab = STATE_ABBR.get(r.get("state", ""), "")
            if ab and ab in allstates:
                r["city"] = city  # consistent
            elif len(allstates) == 1 and tstates:
                r["state"] = ABBR_STATE[next(iter(tstates))]  # unambiguous -> correct state
                r["state_confidence"] = "confirmed"
                r["city"] = city
                fixed_state += 1
            elif len(tstates) == 1:
                r["state"] = ABBR_STATE[next(iter(tstates))]  # one target candidate
                r["state_confidence"] = "confirmed"
                r["city"] = city
                fixed_state += 1
            else:
                r["city"] = ""  # can't reconcile -> drop the contradicting city
                blanked += 1
        pf.write_text(json.dumps(rows, indent=1))
    print(f"reconciled: corrected {fixed_state} states to match city, blanked {blanked} unreconcilable cities")


if __name__ == "__main__":
    main()
