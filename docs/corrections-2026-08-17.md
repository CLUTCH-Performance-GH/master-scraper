# Corrections — 2026-08-17

Things found on first run-through that contradict `docs/NEXT_SESSION_PROMPT.md`
and the README. Recorded per the repo's own rule: what was believed, what was
observed, what was done.

This session ran in a **Claude Code remote container on Linux**, not on the
author's Mac. Several items below follow from that and are environmental, not
defects in the repo. They are separated out.

---

## 1. `requirements.txt` is missing pandas and openpyxl — a real defect

**Believed.** "If a job fails with `ModuleNotFoundError: pandas`, you used the
wrong interpreter."

**Observed.** The interpreter was right and the error still fired. `pandas` is
not in `requirements.txt` at all, and neither is `openpyxl`. A clean
`uv pip install -r requirements.txt` cannot run three tracked scripts:

| Script | Needs |
|---|---|
| `jobs/build_rac_tam.py` | `pandas` (imported, see below) |
| `jobs/rac_analysis.py` | `pandas` (3 real uses) |
| `constructconnect/build_workbook.py` | `pandas` (6 real uses) |

`openpyxl` is needed by the verification step the prompt itself prescribes
(`soffice --convert-to xlsx` then read back with openpyxl).

**Done.** Both added to `requirements.txt`. The diagnostic in
`NEXT_SESSION_PROMPT.md` is misleading and should be retired — a missing
package now means a missing package.

**Also.** `jobs/build_rac_tam.py` imports pandas on line 24 and never uses it;
`pd.` appears nowhere in the file. Left in place for now rather than removed,
because removing it is a change to a script whose output could not be verified
this session (see §3). Worth deleting once the workbook can be rebuilt.

## 2. The repository is public

**Believed.** Stated correctly in the README and `.gitignore` — recorded here
only because it was flagged as a live concern this session and is not yet
resolved.

**Observed.** `GET /repos/CLUTCH-Performance-GH/master-scraper` returns
`"private": false`, `"visibility": "public"`. No secrets or collected data are
tracked — `git ls-files` is clean, and `.gitignore` correctly covers `.env`,
`output/`, `data/` and `*.xlsx`.

**Done.** Nothing, in code. Visibility cannot be changed from this session:
the GitHub MCP surface exposes `create_repository` and `fork_repository` but no
repository-update call, and there is no `gh` CLI. Flipping it is a
repository-settings action for an admin in the GitHub UI.

## 3. The repo alone cannot build the TAM workbook

**Believed.** "Improve the Concrete Masonry Checkoff TAM model in
`output/CMC_TAM_by_RAC_V1.xlsx`, built by `jobs/build_rac_tam.py`."

**Observed.** Neither the workbook nor its inputs exist in a fresh clone, and
by design — `output/` and `data/` are gitignored. `jobs/build_rac_tam.py` opens
`data/rac_rollup.json` unguarded on its first line of work and dies:

```
ROLLUP    = data/rac_rollup.json        absent  -> hard crash
PRODUCERS = data/producers_national.json absent  -> guarded by .exists()
CENSUS    = data/census_cbp_rac.json     absent  -> guarded by .exists()
```

So the model is not "improvable in place" from a clone. It has to be rebuilt
from its inputs, and two of the three inputs are themselves derived artefacts.

**Done.** Confirmed `data/rac_rollup.json` is **reconstructable offline** from
the ConstructConnect V3 workbook supplied this session: its Projects sheet
carries State, Project Value, Product Fit and both product-scope columns for
all 6,182 projects, and summing project value reproduces the deliverable's own
Summary figure of $1,246,541,321,771 exactly. `data/census_cbp_rac.json` is
**not** reconstructable here (§4). `data/producers_national.json` is not
reconstructable at all without a Serper/Places sweep.

Recommendation for whoever next touches `build_rac_tam.py`: guard the `ROLLUP`
read the same way the other two are guarded, and fail with a message naming the
job that produces it.

## 4. Environmental — outbound network policy blocks every data source

Not a repo defect. Recorded so the next remote session does not spend time
rediscovering it.

The container's egress proxy enforces an allowlist. Measured this session:

| Host | Needed for | Result |
|---|---|---|
| `api.census.gov` | step 1, value of shipments | **403 CONNECT — policy denial** |
| `www2.census.gov` | existing `census_cbp_rac.py` flat file | **403 CONNECT — policy denial** |
| `google.serper.dev` | the whole contact ladder | **403 CONNECT — policy denial** |
| `app.constructconnect.com` | any live pull | **403 CONNECT — policy denial** |
| `api.anthropic.com` | LLM extraction rung | reachable |
| `github.com` | push | reachable |

The proxy README is explicit that policy denials must be reported rather than
retried or routed around, so no workaround was attempted. Steps 1–5 of the TAM
brief all require at least one blocked host. Offline work on supplied files is
unaffected.

Note also that a browser-driven ConstructConnect pull is not merely blocked but
architecturally wrong from a container: `docs/constructconnect.md` §1 has the
harvester run *inside the author's authenticated tab* specifically so session
state never leaves the browser. A remote container has no such session.

## 5. Environmental — the documented setup is macOS-only

`uv venv --python /opt/homebrew/bin/python3` cannot run here; there is no
Homebrew. The LibreSSL 2.8.3 / `bad record mac` hazard is an Apple-system-Python
problem and does not apply to this image.

Used instead, with the same result:

```bash
uv venv --python "$(command -v python3)" .venv     # CPython 3.11.15
VIRTUAL_ENV=.venv uv pip install -r requirements.txt
```

`soffice` is present, so the prescribed recalculate-don't-eyeball verification
works unchanged.

## 6. The V3 deliverable's distinct-address count is inflated by case variants

**Believed.** The V3 Summary tab reports "Distinct personal addresses
(deduplicated): 8,756".

**Observed.** That figure deduplicates addresses **as written**, without
case-folding. Email domains are case-insensitive by specification and local
parts are case-insensitive at every mainstream provider, so a handful of
addresses are counted twice under case variants:

| Count | Basis |
|---|---|
| 8,756 | as written — what the Summary tab reports |
| **8,708** | lower-cased — the true distinct count |
| 8,231 | lower-cased, on named-person rows only |

48 addresses appear under more than one capitalisation — the shape is
`aExample@` alongside `aexample@`, which is one mailbox counted twice. The
overstatement is 0.55%, so no conclusion changes, but the number is wrong as
published.

(The real examples are not reproduced here. They are live addresses belonging
to named individuals in the vendor extract, and this repository is public.)

**Done.** `jobs/contacts_by_rac.py` lower-cases before deduplicating. The
existing `load_external_exclusions` in `jobs/build_workbook.py` already
lower-cases, so de-duplication against Dennis's list was never affected — only
the reported total. Worth correcting the next time the deliverable is rebuilt.

## 7. Distinct people do not add up across regions

Not a defect, but a trap worth naming because the workbook now publishes both
numbers side by side.

Summing per-region distinct-people counts gives 12,199. The true national
figure is the union, 12,037: 162 named people appear on projects in more than
one region and are legitimately distinct in each. Contact **rows** add up;
distinct **people** do not. The Contacts by RAC tab writes the union on its own
NATIONAL row and says in a note that the column does not sum to it.

The same distinction matters for the headline: 23,420 contact rows are 12,037
callable people, and only 6,391 of those sit on projects carrying an assessable
masonry scope code.

## 8. Minor — stale remote in the onboarding prompt

`docs/NEXT_SESSION_PROMPT.md` line 9 points at `github.com/jacklumpe/master-scraper`.
The actual remote is `github.com/CLUTCH-Performance-GH/master-scraper`. Left
alone this session to avoid editing the prompt someone may be mid-way through
following; worth correcting.
