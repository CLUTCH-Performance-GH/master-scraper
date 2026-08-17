"""Run the remaining companies sequentially (fresh clients per company so the
per-process Serper budget resets each time). One process, one log.

Usage: PYTHONPATH=. python3 jobs/run_all_remaining.py [key key ...]
       (default: every configured company except nutrien + chs)
"""
import sys
import traceback

from jobs.contacts_lib import TARGET_STATES
from jobs.run_company import COMPANIES, run_company

keys = sys.argv[1:] or [k for k in COMPANIES if k not in ("nutrien", "chs")]
print(f"running {len(keys)} companies: {', '.join(keys)}")
for k in keys:
    print(f"\n########################## {k} ##########################", flush=True)
    try:
        run_company(k, TARGET_STATES, corroborate=True)
    except Exception as e:
        print(f"!!! {k} FAILED: {type(e).__name__}: {e}")
        traceback.print_exc()
print("\n=== ALL REMAINING COMPANIES DONE ===")
