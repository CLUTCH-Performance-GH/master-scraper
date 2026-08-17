# Master Scraper

A contact and company intelligence toolkit. Given a list of companies, it finds
the people worth calling, with a confidence label on every field so you know what
is confirmed and what is a best guess.

Built at CLUTCH Performance for B2B lead work in building materials and
agriculture.

> **No collected data is in this repository.** Everything the toolkit gathers is
> real people's business contact details, which is personal data under CCPA/CPRA
> and PIPEDA, and much of it is licensed from vendors. `output/` and `data/` are
> gitignored. Clone this, add your own keys, and it collects its own.

---

## The idea

Contact enrichment is a cost ladder. Most tools start at the expensive rung. This
one starts at the cheapest and stops as soon as it has an answer:

```
cache  ->  free HTTP  ->  Serper (~$0.0003/query)  ->  Firecrawl  ->  LLM
```

A run over 314 companies producing 1,303 named contacts cost about 1,000 Serper
queries, **zero Firecrawl credits**, and a few cents of Haiku.

## Every field carries its provenance

Nothing is asserted that cannot be traced. Emails are labelled:

| Label | Means |
|---|---|
| `verified` | The address appeared on the company's own website |
| `linkedin` | Taken from a public profile |
| `inferred (pattern, N samples)` | Constructed from the company's observed format, from **at least 2 genuine samples**, and MX-checked |

If a pattern rests on fewer than two real samples, **no address is emitted**.
Email-format directory sites publish worked examples (`jdoe@`, `flast@`) that look
like real staff; trusting them produces confidently wrong addresses. A missing
address costs less than a wrong one.

## Layout

```
msc/                    the framework
  net.py                HTTP, Serper, Firecrawl clients with rate limits + budgets
  cache.py              SQLite response cache; re-runs are nearly free
  extract.py            emails, phones, addresses, JSON-LD, sitemaps
  enrich.py             the contact ladder
  llm.py                Claude extraction against strict JSON schemas
  dedupe.py             entity resolution
  validate.py           contract checks and the Ground Truth Protocol audit
  workbook.py           branded Excel output
jobs/                   runnable pipelines
  run_cmu_contacts.py   contact discovery for a company list
  build_cmu_workbook.py the client deliverable
  run_company.py        per-company discovery with state confirmation
constructconnect/       ConstructConnect Insight ingestion
  browser/              the in-page harvester
docs/                   how each pipeline works and what it cost
```

## Setup

Use a virtualenv on Homebrew Python. Apple's system 3.9 ships LibreSSL 2.8.3,
which throws intermittent `bad record mac` errors on TLS-heavy runs.

```bash
uv venv --python /opt/homebrew/bin/python3 .venv
VIRTUAL_ENV=.venv uv pip install -r requirements.txt

cp .env.example .env      # then add your keys
```

Keys are read from `.env` and never hardcoded. `SERPER_API_KEY` and
`ANTHROPIC_API_KEY` are the useful ones; Firecrawl is optional and rarely needed.

```bash
PYTHONPATH=. .venv/bin/python jobs/run_cmu_contacts.py --limit 5
```

## Design rules worth knowing

**Checkpoint everything.** Every job writes per-company JSON and resumes from it.
Runs get interrupted; machines sleep; tabs close.

**Fail loudly.** A wrong contact in a CRM costs more than a missing one, so the
code refuses to guess rather than quietly producing something plausible.

**Respect the source.** Single session, rate-limited well under any published
ceiling, never parallelised across logins. A locked account costs more than a late
dataset.

**Reality beats the spec.** When an assumption turns out wrong, the correction and
its evidence go in `docs/`, not just a code comment. See the 403 section of
`docs/constructconnect.md` for the clearest example.

## Documentation

- [`docs/constructconnect.md`](docs/constructconnect.md) — Insight ingestion: the
  10,000-record ceiling, what a 403 really means, the Excel repair traps
- [`docs/cmu-producers.md`](docs/cmu-producers.md) — building a producer contact
  list from scratch, and the bugs that cost real contacts
- [`docs/NEXT_SESSION_PROMPT.md`](docs/NEXT_SESSION_PROMPT.md) — a copy-paste
  prompt for picking up the CMC TAM work, written to onboard someone new

## Licence

MIT. See [LICENSE](LICENSE).
