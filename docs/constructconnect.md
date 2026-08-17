# ConstructConnect Insight ingestion

How the ConstructConnect pull works, what it cost, and every constraint found the
hard way. Written so the next run does not rediscover any of it.

**Status:** 6,182 projects and 23,420 contacts collected across three rounds.
Round 3 is parked at 1,184 of 2,500. Targets for the remainder are on disk, so
resuming needs no rediscovery.

---

## 1. Acquisition: the app's own JSON API

Insight is a Next.js SPA that talks to a private JSON API. Reading that API
directly beats scraping the DOM, which re-renders on every deploy.

| Endpoint | Method | Gives |
|---|---|---|
| `searchAPI/projectLeadsElastic` | POST | Search + discovery, 25 fields per project |
| `project/getProjectDataByCrmId` | POST | Full project detail, ~60 fields |
| `projectParticipants/getDesignTeam` | POST | **The contacts** |

Auth is a session cookie plus a rotating `CSRF-Token` header. Rather than export
live session state into Python, the harvester runs inside the authenticated tab
so the browser supplies both. See `constructconnect/browser/cc_harvester.js`.

Two details that cost time to find:

- The request body is an **array-wrapped object**: `[{limit, offset, filters…}]`.
  Reuse the saved search's own captured body verbatim rather than rebuilding the
  filters, which are large (6 stages, 41 categories, 623 CSI codes).
- `getDesignTeam` keys on the **internal** project id (`p.Id` from the detail
  response), not the search id. They differ. Passing the search id returns 500.

## 2. The hard ceiling that shapes everything

```
limit          <= 100
offset + limit <= 10,000
```

Elasticsearch's `max_result_window`. Against a 136,587-project saved search,
**no single query can reach the whole population.** Verified: offset 9,900 with
limit 100 returns 200; offset 9,901 returns 400.

That forces partitioning:

| Round | Slice | Projects |
|---|---|---|
| 1 | offsets 0–4,900, `lastUpdatedDate desc` | 2,499 |
| 2 | offsets 5,000–9,900, exhausting that window | 2,499 |
| 3 | Custom Date Range filter on Last Updated | 1,184 of 2,500 |

After round 2 the sort window is spent. Reaching deeper **requires** a date or
region partition where each slice returns under 10,000.

## 3. Ranking, because you cannot pull 136,000

Detail plus contacts costs 2 API calls per project. At a polite rate that is the
binding constraint. `classify.py` scores 0–100:

- project value, up to 40 points
- recency of last update, up to 35
- product fit, up to 25

Unreported value scores 4 rather than 0. A blank value field is common in this
data and is not evidence of a small job.

## 4. The product-fit trap

Fit keys on MasterFormat **subsection** codes only: rebar 03 21, structural steel
05 12, retaining walls 32 32, CMU 04 22, and so on.

An earlier version keyed on section-level rollups and labelled nearly every
project a CMC lead. Measuring across the pull showed why:

| Rollup code | Appears on |
|---|---|
| 04 20 Unit masonry | **100%** of projects |
| 05 10 Structural metal framing | 99% |
| 03 20 Concrete reinforcing | 98% |

A signal present on 98% of records cannot discriminate. Rollups now contribute at
most 3 corroborating points and never qualify a project alone.

## 5. What a 403 actually means

**Not a rate limit and not a ban.** A 403 from `getProjectDataByCrmId` is
per-project: the subscription does not license that record.

Verified back to back in one session, same endpoint, same headers: two ids
returned 200 while `7642912` returned 403 every time, across hours.

This was initially misread as vendor flakiness, and the crawler's stop-on-any-403
rule cost hours of progress. Correct handling: log it, skip the project, continue.
Stop only on repeated 403s **clustering with no progress between them**, which is
what an actual account-level refusal looks like.

The account was never rate limited, warned, or flagged across ~15,000 calls.

## 6. Rate discipline

Single session, 11–16 requests/minute with randomised spacing, against the 20/min
ceiling in `config/rate_limits.yaml`. Never parallelised across logins.

A silent `AudioContext` oscillator keeps the tab off Chrome's background timer
throttle. Without it a backgrounded tab drops to roughly one timer per minute and
the run silently stalls.

## 7. Durability

Records live in page memory until drained. Two incidents proved the discipline:

- A machine sleep dropped the network mid-run. Two transient fetch failures,
  both refetched, nothing lost.
- The browser tab was closed. Everything since the last drain was gone; the
  target list on disk made resumption exact.

Rules that follow:

1. Drain every ~45 minutes. Exposure is capped at one window, roughly 300 records.
2. Mark records saved **only after** the file on disk parses. A failed clipboard
   transfer then costs a retry, never data.
3. Namespace each round's files (`details_r2_*`, `details_r3_*`). Round 2's batch
   counter restarts at zero and would otherwise overwrite round 1.

## 8. Known data quirks

- **Cross-partition duplicates.** Each round returned one project twice at a
  partition boundary. Dedupe by id; last write wins.
- **An off-by-one in the worker** double-fetched one project and skipped another
  per round. Excluding on *pulled* ids rather than *targeted* ids self-heals it.
- **Truncated source values.** One phone reads `(832) 652-` because that is what
  ConstructConnect holds. Passed through rather than invented.
- **Search summaries are a separate pass.** A project pulled in a later round has
  its summary in that round's discovery file. `build_workbook.py` joins them from
  disk; without it, Product Fit silently reads unclassified.

## 9. Excel will demand a repair if you let it

Two distinct causes, both fixed in `build_workbook.py`:

1. **Malformed hyperlink targets.** The vendor ships values like
   `http:// www.tlc-engineers.com`. Excel drops the **entire sheet's** hyperlink
   table rather than the one bad link. Fix: `strings_to_urls: False`, then write
   links explicitly only after validation.
2. **The 65,530-per-sheet hyperlink ceiling.** At 19k contacts the Contacts sheet
   was already at 48k. A 60,000 budget now downgrades the excess to plain text.

`verify_links.py` checks the raw sheet XML rather than trusting the writer.

## 10. The better long-term source

ConstructConnect sells **CRM Integration** (formerly DataLink): a sanctioned daily
XML export over FTP, driven by a saved search, enabled by your account manager.

It carries two things the web UI never exposes:

- pre-computed change history (`UpdateSummarizations`)
- `Materials/Material` with MasterFormat codes, the real answer to "what products
  are on this job"

Plus no session fragility and no ToS exposure. Reference "CRM Format 1.2.1". Ask
whether the contract permits loading into an internal platform and showing derived
aggregates to clients, and get it in writing.

## 11. Compliance

The output contains named individuals with business emails and direct phones.
Business contact data is still personal data under CCPA/CPRA and PIPEDA. Treat
distribution as controlled and confirm permitted use before it becomes an outbound
list. **That question is open as of this writing.**

---

## Running it

```bash
# 1. discovery + detail collection happen in the browser
#    paste constructconnect/browser/cc_harvester.js into the console
#    on an authenticated app.constructconnect.com tab

# 2. choose which projects to pull in full
python3 constructconnect/select_round3.py

# 3. build the workbook from the drained JSON
python3 constructconnect/build_workbook.py

# 4. confirm Excel will open it without a repair prompt
python3 constructconnect/verify_links.py
```
