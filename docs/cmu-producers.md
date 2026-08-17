# Building a producer contact list from scratch

A worked example: turning a 153-company spreadsheet into 1,303 named contacts
across 314 concrete masonry producers in twelve northeastern states.

Kept because the failure modes here generalise to any company-list enrichment.

---

## The starting point

A partner list of 153 companies with contact names but **no websites and no
domains**, which was under-matching on LinkedIn's company-list upload.

Two things were costing matches before any new data was collected:

1. **Every one of the 153 had a recoverable email domain** sitting in its own
   contact emails. The upload was discarding it. LinkedIn matches on domain far
   more reliably than on name.
2. **47 names carried location suffixes** — `Ideal Concrete Block Co - Westford,
   MA`. LinkedIn matches literally, so those were near-guaranteed misses.

Fixing the input beat enriching it. Always check the existing data before
collecting more.

## Finding the rest of the universe

| Source | Yield | Note |
|---|---|---|
| Partner list | 153 | starting point |
| CMHA producer directory | +36 new | members only; `?address_state=XX` filters it |
| Google Places, 7 terms x 12 states | **+167 new** | the big one |

The association directory only lists **paying members**. Independent block plants
belonging to neither list were invisible to both. Places knows them because they
are physical businesses.

Filtering matters more than finding. A masonry **contractor** is not a producer,
and neither is a landscaper or a big-box store. Places queries need both a
positive filter (block, masonry, precast, paver, aggregate) and a negative one
(contractor, landscaping, rental, home improvement).

## Four bugs that each cost real contacts

Recorded because all four were silent. None raised an error; each simply produced
less than it should have.

**1. Every LLM call was returning `None`.** The client sent an
`output_config.effort` parameter that Haiku 4.5 rejects with a 400. The framework
swallowed it and returned `None`, so the entire website-extraction rung was dead
while looking like it worked. Fixed to detect rejection from the API rather than a
hardcoded model list.

**2. Truncation cut off the answer.** Page text was capped at 60,000 characters
before parsing. These homepages run to 700KB and **contact details live in the
footer**, past the cut. Result: zero verified emails. Regex the full text, and
truncate only the LLM prompt.

**3. Guessed URL paths 404.** `/contact` and `/about` do not exist on most small
business sites; they use `/contact-us/` or a CMS permalink. Reading the
homepage's own nav links finds the real URLs.

**4. Requiring a surname discarded verified people.** Many sites list staff as
`Bruce - Sales, bruce@company.com`. A surname is only needed to *infer* an
address; when the address is already verified the contact is fully actionable.
One company went from 6 anonymous rows to 6 named sales staff.

## Recovering names from addresses

A verified address often *is* a name. Roughly 233 contacts were recovered this
way, in descending confidence:

| Shape | Example | Result |
|---|---|---|
| `first.last` | `anna.fischer@` | Anna Fischer |
| matches a known contact | `johnbock@` + LinkedIn "John Bock" | John Bock |
| name + company surname | `johnbock@bockbrick.example` | John Bock |
| initial + surname | `jdoherty@` | J. Doherty *(partial)* |
| initials only | `acm@`, `gdh@` | left blank |

Nothing is invented. Only what the local part literally spells is used, and the
partial cases say so.

## Two data-quality traps

**Addresses scraped from `mailto:` links carry URL encoding.**
`%20jsmith@example-precast.com` and `-recovery@example-cement.com` would bounce
exactly as written. Strip percent-encoding and leading punctuation before anything
else reads the address.

**Shared inboxes are not contacts.** `info@`, `sales@`, `questions@`, `ap@`,
`careers@` are deliverable but nobody works them. They inflate a count without
adding a workable lead. The framework's built-in generic list is only 12 entries;
it needs extending. Where a named person sits behind one, keep the person and drop
the address.

## Where it landed

| | |
|---|---|
| Producers | 314 |
| Contact rows | 1,314 |
| **Named people** | **1,303 (99.2%)** |
| Verified emails | 249 |
| Inferred emails | 379 |
| With phone | 253 |
| Owners / Presidents | 345 |
| VPs / Directors / Managers | 226 |

Cost: ~1,075 Serper queries, zero Firecrawl credits, a few cents of Haiku.

## Location is its own problem

Only 83 of 176 producers arrived with a state. Two passes fixed it:

1. read the postal address off already-cached site text — free, +37
2. Google Places lookup for the remainder — 56 queries, +39

Final: 159 of 176. Worth knowing that **25 companies in a "Region 1 Northeast"
list turned out to be headquartered in CA, TX, MI and elsewhere** — national
brands rather than regional producers. They get their own tab rather than being
silently dropped or silently counted.
