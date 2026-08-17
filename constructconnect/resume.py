"""
Work out what is still missing and emit the remaining queue.

The crawler holds its state in the browser tab, so a closed tab or a reloaded page
loses the in-memory queue position. Everything already written to data/details_*.json
survives, though, so the remaining work is simply
    targets.txt  minus  the ids present on disk.

Run:  python3 resume.py            report only
      python3 resume.py --emit     also print the remaining ids as a JS array literal
      python3 resume.py --emit 900 cap the emitted slice at 900 ids
"""

import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")


def ids_on_disk():
    have, bad = set(), []
    for path in sorted(glob.glob(os.path.join(DATA, "details_*.json"))):
        try:
            with open(path) as fh:
                chunk = json.load(fh)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            bad.append((os.path.basename(path), str(exc)[:70]))
            continue
        for rec in chunk:
            if rec.get("id"):
                have.add(str(rec["id"]))
    return have, bad


def main():
    targets = [l.strip() for l in open(os.path.join(DATA, "targets.txt")) if l.strip()]
    have, bad = ids_on_disk()

    # A record can land on disk with p=None if the detail call failed; those are
    # not complete and should be retried rather than counted as done.
    complete = set()
    for path in sorted(glob.glob(os.path.join(DATA, "details_*.json"))):
        try:
            chunk = json.load(open(path))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        for rec in chunk:
            if rec.get("id") and rec.get("p"):
                complete.add(str(rec["id"]))

    missing = [t for t in targets if t not in complete]
    partial = sorted(have - complete)

    print(f"targets:            {len(targets):,}")
    print(f"complete on disk:   {len(complete):,}")
    print(f"landed but no data: {len(partial):,}  (will be retried)")
    print(f"still to fetch:     {len(missing):,}")
    print(f"requests remaining: {len(missing) * 2:,}")
    if bad:
        print("\nunreadable files (fix or delete these):")
        for name, err in bad:
            print(f"  {name}: {err}")

    if "--emit" in sys.argv:
        cap = None
        for a in sys.argv[sys.argv.index("--emit") + 1:]:
            if a.isdigit():
                cap = int(a)
                break
        slice_ = missing[:cap] if cap else missing
        print(f"\n// {len(slice_)} ids")
        print('"' + ",".join(slice_) + '"')


if __name__ == "__main__":
    main()
