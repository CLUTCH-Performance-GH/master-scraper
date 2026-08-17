# Next session prompt

Copy everything between the rules into a fresh Claude Code session started in the
`Master Scraper` directory. It is written to be self-contained, so someone who
has never opened this repo can run it.

---

I am working in the Master Scraper repo (github.com/jacklumpe/master-scraper).
This is my first time using it, so verify things rather than assuming, and tell
me when something you find contradicts what I have written below.

## What this repo is

A contact and company intelligence toolkit. Given a list of companies it finds
the people worth calling, and labels the provenance of every field. Read
`README.md`, then `docs/constructconnect.md` and `docs/cmu-producers.md` before
writing any code. Those two docs record constraints that were expensive to
discover and will waste your time if you rediscover them.

## Set up first, and do not skip this

The system `python3` on this Mac is Homebrew 3.14 and has almost no packages.
Apple's `/usr/bin/python3` is 3.9 with LibreSSL 2.8.3, which throws intermittent
`bad record mac` TLS errors on API-heavy runs. Use the project venv:

```bash
uv venv --python /opt/homebrew/bin/python3 .venv
VIRTUAL_ENV=.venv uv pip install -r requirements.txt
```

Run everything as `PYTHONPATH=. .venv/bin/python jobs/<script>.py`. If a job
fails with `ModuleNotFoundError: pandas`, you used the wrong interpreter.

Keys live in `.env`, never in source. Copy `.env.example` to `.env` and fill in
`SERPER_API_KEY` and `ANTHROPIC_API_KEY`. Firecrawl is optional and rarely worth
the credits. Confirm they load before starting:

```bash
.venv/bin/python -c "from msc import settings; import os; print({k: bool(os.getenv(k)) for k in ['SERPER_API_KEY','ANTHROPIC_API_KEY','FIRECRAWL_API_KEY']})"
```

## The job

Improve the Concrete Masonry Checkoff TAM model in
`output/CMC_TAM_by_RAC_V1.xlsx`, built by `jobs/build_rac_tam.py`. It sizes the
checkoff by Regional Advisory Council. National totals are solid; the regional
split is modelled and is the weak part. Work these in order and stop to show me
results after each one.

**1. Census value of shipments by state (highest value).**
The model currently allocates national volume across regions using Census County
Business Patterns *employment* for NAICS 327331, Concrete Block and Brick
Manufacturing. See `jobs/census_cbp_rac.py`. Employment is a proxy for output.
*Value of shipments* is much closer to actual units. Pull it from the 2022
Economic Census for NAICS 327331 by state. That needs a free Census API key from
api.census.gov/data/key_signup.html, which takes two minutes. Add it to `.env` as
`CENSUS_API_KEY` and add a `Census Shipments` option to the allocation basis.

**2. Convert dollars to units.**
Find a defensible average selling price per concrete masonry unit. Industry
publications, producer price lists and the Producer Price Index series for
concrete block are all reasonable starting points. With a price, shipments by
state converts straight into units by state and the allocation stops being a
proxy at all. Put the price on the Assumptions tab as an editable input, and show
me the sources you considered and why you chose the one you did.

**3. Weight by plant size.**
CBP publishes establishment counts by employee size band. A region of large
plants produces very differently from a region with the same number of small
ones. Add a size-weighted basis.

**4. Fix the demand-side sample.**
`docs/constructconnect.md` explains this. The 6,182 ConstructConnect projects
were selected for size and recency out of roughly 136,000, so the value
distribution is skewed toward large projects and the project-value basis is not
trustworthy. Either pull a stratified random sample or clearly mark those bases
as indicative only.

**5. Check CMC's own published material.**
concretemasonrycheckoff.org announced 27 approved programs. If program budgets
are published by region, that reveals actual regional spend and back-solves
toward actual regional collections, which would replace the modelled split with
something close to fact. Worth twenty minutes on their newsroom, board minutes
and any annual report.

## Facts you can rely on, so do not re-research them

- Assessment is **$0.01 per assessable unit**, since 1 April 2023.
- Assessable means dry-cast units, made on mechanised block machines, **3 inches
  or more in actual width**, for masonry construction.
- **Pavers, segmental retaining wall units, clay brick, precast lintels and
  anything under 3 inches are EXEMPT.** This matters more than it sounds: a
  hardscape-heavy producer contributes far less than its size suggests.
- Collections run to roughly **$10M a year**, confirmed by the CMCB.
- **At least 50%** of program dollars return to the region that generated them.
- Region 5 is AK, CA, CO, HI, ID, MT, NV, OR, UT, WA, WY (verbatim from the
  Commerce Order). Region 1 is CT, DE, ME, MD, MA, NH, NJ, NY, PA, RI, VT, WV.
  Regions 2, 3 and 4 were read off the CMC regional map and are on the editable
  State Mapping tab. If you find an authoritative list, correct it there.
- We do **not** have CMCB access. Do not build anything that depends on it.

## How to work

**Never trust a scraped count as a denominator.** This bit us already. A
business-listing sweep put Region 1 at 27.2% of US producers; Census says 18.1%.
The gap was our own search effort, because Region 1 had been swept harder for an
earlier task. A search measures how hard you looked. Wherever our own collection
becomes a percentage, find a census to check it against.

**Rate limits are a hard ceiling, not a target.** Serper and Firecrawl budgets
are in `.env`. Single session, never parallelise across logins. A locked account
costs more than a late dataset.

**Checkpoint everything.** Every job writes per-company JSON and resumes from it.
Runs get interrupted, machines sleep, tabs close.

**Excel output has two traps**, both already solved in `jobs/build_cmu_workbook.py`
and worth copying: malformed hyperlink targets make Excel drop a whole sheet's
links and demand a repair, and there is a hard ceiling of 65,530 hyperlinks per
sheet. Also, xlsxwriter writes formulas with **no cached value**, so always pass
the computed value as the last argument to `write_formula` or colleagues with
auto-calculation off will open the file to a grid of zeros.

**Verify by recalculating, not by eyeballing.** After building a workbook:

```bash
soffice --headless --convert-to xlsx --outdir /tmp/check output/<file>.xlsx
.venv/bin/python -c "import openpyxl; wb=openpyxl.load_workbook('/tmp/check/<file>.xlsx', data_only=True); ..."
```

**The repo is public.** `output/` and `data/` are gitignored because they hold
real people's business contact details and vendor-licensed extracts. Never commit
collected data, never commit `.env`, and check that example addresses in code
comments or docs are not real people's.

## Definition of done

- The allocation basis is driven by shipments or units rather than headcount,
  or you can explain with evidence why that was not achievable.
- Every assumption is an editable cell with a source next to it.
- The sensitivity block shows the spread across all bases, so a reader can see
  which regions are uncertain.
- The Sources tab says plainly what is measured and what is modelled.
- The workbook opens without a repair prompt and shows real numbers immediately.
- Anything you learn that contradicts this prompt is written into `docs/`, with
  the date, what was believed, what you observed and what you did about it.

Start by reading the docs and confirming the setup works. Then show me your plan
for step 1 before you build it.
