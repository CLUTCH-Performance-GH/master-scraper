"""
Product-fit classification and lead scoring for ConstructConnect project pulls.

Fit is derived from the project's MasterFormat (CSI) code list, which the search
API returns per project. Codes are matched by prefix so a project tagged with
either the section rollup (032000) or a specific subsection (032116) both count.

Two client scopes are modelled:
  CMC  - reinforcing steel and structural steel (Commercial Metals-type products)
  SRW  - segmental retaining wall, hardscape and concrete masonry units

Every prefix carries a weight. A project's scope score is the weighted sum of the
distinct prefixes it hits, so a job that specifies rebar AND structural steel
scores higher than one that only mentions metal fabrications.
"""

from decimal import Decimal, ROUND_HALF_UP

# Measured against 102 live projects: ConstructConnect applies the SECTION ROLLUP
# codes almost universally as generic scope tags. Observed frequencies:
#
#   0420 Unit masonry            100%    0510 Structural metal framing   99%
#   0550 Metal fabrications       99%    0330 Cast-in-place concrete     97%
#   0320 Concrete reinforcing     98%    0540 Cold-formed framing        92%
#
# A signal present on 98% of projects cannot discriminate. Keying "in scope" on
# those codes labelled essentially every project as a CMC lead, which is worse than
# useless because it looks like an answer. The SUBSECTION codes are the real
# signal, and they are appropriately selective:
#
#   0321 Reinforcement bars      11%     3232 Retaining walls            17%
#   0512 Structural steel        27%     0422 Concrete unit masonry      26%
#   0531 Steel decking           18%     3214 Unit paving                16%
#
# So: subsection codes carry the weight, rollups are kept at weight 1 as weak
# corroboration only, and a scope requires at least one PRIMARY hit to qualify.

# --- CMC scope: rebar first, then structural steel -------------------------
CMC_PRIMARY = {
    "0321": ("Reinforcement bars (rebar)", 10),
    "0322": ("Fabric and grid reinforcing", 8),
    "0323": ("Stressed tendon / post-tension", 7),
    "0512": ("Structural steel framing", 8),
    "0531": ("Steel decking", 6),
    "0521": ("Steel joist framing", 6),
    "0341": ("Precast structural concrete", 5),
}
CMC_WEAK = {
    "0320": ("Concrete reinforcing (generic)", 1),
    "0510": ("Structural metal framing (generic)", 1),
    "0520": ("Metal joists (generic)", 1),
    "0530": ("Metal decking (generic)", 1),
    "0540": ("Cold-formed metal framing (generic)", 1),
    "0550": ("Metal fabrications (generic)", 1),
    "0330": ("Cast-in-place concrete (generic)", 1),
    "0340": ("Precast concrete (generic)", 1),
}

# --- SRW scope: retaining wall, hardscape, CMU -----------------------------
SRW_PRIMARY = {
    "3232": ("Retaining walls", 10),
    "0422": ("Concrete unit masonry (CMU)", 9),
    "3214": ("Unit paving / hardscape", 7),
    "0426": ("Single-wythe unit masonry", 6),
}
SRW_WEAK = {
    "0420": ("Unit masonry (generic)", 1),
    "0421": ("Clay unit masonry", 1),
    "3231": ("Fences, gates and site walls", 1),
    "3205": ("Cement and concrete for exterior improvements", 1),
    "3292": ("Turf and grasses / site finish", 1),
}

# kept for reporting and backward compatibility
CMC_PREFIXES = {**CMC_PRIMARY, **CMC_WEAK}
SRW_PREFIXES = {**SRW_PRIMARY, **SRW_WEAK}

# Codes worth surfacing even though they are not a direct product sale: they
# indicate the project has sitework/structure scope at all.
STRUCTURE_HINT_PREFIXES = {"03", "04", "05", "31", "32"}


def _match(codes, table):
    """Return (score, [labels]) for the prefixes in `table` that `codes` hits."""
    hits = {}
    for code in codes:
        c = str(code).replace(" ", "").replace(".", "")
        for prefix, (label, weight) in table.items():
            if c.startswith(prefix):
                # keep the highest-weight label per prefix, count each prefix once
                hits[prefix] = (label, weight)
    score = sum(w for _, w in hits.values())
    labels = [lbl for lbl, _ in sorted(hits.values(), key=lambda x: -x[1])]
    return score, labels


def classify_fit(csi_codes):
    """
    Classify a project's fit against the CMC and SRW product scopes.

    A scope qualifies only on a PRIMARY (subsection) code hit. Rollup codes add at
    most a point of corroboration, because they are present on nearly every project
    and so carry no information on their own.
    """
    codes = [c for c in (csi_codes or []) if c]
    cmc_p, cmc_p_lbl = _match(codes, CMC_PRIMARY)
    cmc_w, cmc_w_lbl = _match(codes, CMC_WEAK)
    srw_p, srw_p_lbl = _match(codes, SRW_PRIMARY)
    srw_w, srw_w_lbl = _match(codes, SRW_WEAK)

    cmc_score = cmc_p + min(cmc_w, 3)   # cap the generic contribution
    srw_score = srw_p + min(srw_w, 3)

    has_structure = any(
        str(c).replace(" ", "")[:2] in STRUCTURE_HINT_PREFIXES for c in codes
    )

    if cmc_p > 0 and srw_p > 0:
        fit = "Both (CMC + SRW)"
    elif cmc_p >= 6:
        fit = "CMC - reinforcing / structural steel"
    elif srw_p >= 6:
        fit = "SRW - retaining wall / hardscape / CMU"
    elif cmc_p > 0:
        fit = "CMC - secondary steel scope"
    elif srw_p > 0:
        fit = "SRW - secondary hardscape scope"
    elif has_structure:
        fit = "Generic scope tags only - verify before working"
    else:
        fit = "Out of scope"

    return {
        "fit": fit,
        "cmc_score": cmc_score,
        "srw_score": srw_score,
        "cmc_products": "; ".join((cmc_p_lbl + cmc_w_lbl)[:6]),
        "srw_products": "; ".join((srw_p_lbl + srw_w_lbl)[:6]),
        # only a primary hit counts as genuinely in scope
        "in_scope": (cmc_p > 0 or srw_p > 0),
    }


# --- value banding, per cc-ingest-spec/taxonomies/value_bands.json ----------
VALUE_BANDS = [
    ("Under $250K", None, 250_000, 10),
    ("$250K to $1M", 250_000, 1_000_000, 20),
    ("$1M to $5M", 1_000_000, 5_000_000, 30),
    ("$5M to $10M", 5_000_000, 10_000_000, 40),
    ("$10M to $25M", 10_000_000, 25_000_000, 50),
    ("$25M to $50M", 25_000_000, 50_000_000, 60),
    ("$50M to $100M", 50_000_000, 100_000_000, 70),
    ("$100M and above", 100_000_000, None, 80),
]


def value_band(value):
    """Half-open [lo, hi) banding. Null value is its own band, per the spec."""
    if value is None or value == "" or (isinstance(value, (int, float)) and value <= 0):
        return "Value not reported", 999
    v = float(value)
    for label, lo, hi, order in VALUE_BANDS:
        if (lo is None or v >= lo) and (hi is None or v < hi):
            return label, order
    return "Value not reported", 999


def _round_half_up(x, places=0):
    """CLUTCH standard: ROUND_HALF_UP, not Python's banker's rounding."""
    q = Decimal(1).scaleb(-places)
    return float(Decimal(str(x)).quantize(q, rounding=ROUND_HALF_UP))


def priority_score(value, days_since_update, fit_info):
    """
    Lead priority on a 0-100 scale. Weighted to 'large and recent first' as
    requested, but recency alone can carry a small project onto the list.

      Value size      up to 40 pts
      Recency         up to 35 pts
      Product fit     up to 25 pts
    """
    # value: log-ish ladder so a $500M job does not swamp everything
    v = float(value or 0)
    if v >= 100_000_000:
        v_pts = 40
    elif v >= 50_000_000:
        v_pts = 36
    elif v >= 25_000_000:
        v_pts = 32
    elif v >= 10_000_000:
        v_pts = 27
    elif v >= 5_000_000:
        v_pts = 22
    elif v >= 1_000_000:
        v_pts = 16
    elif v >= 250_000:
        v_pts = 10
    elif v > 0:
        v_pts = 6
    else:
        v_pts = 4  # unreported value is common and not a disqualifier

    # recency: a small project updated yesterday should still surface
    d = days_since_update
    if d is None:
        r_pts = 8
    elif d <= 1:
        r_pts = 35
    elif d <= 3:
        r_pts = 32
    elif d <= 7:
        r_pts = 28
    elif d <= 14:
        r_pts = 23
    elif d <= 30:
        r_pts = 17
    elif d <= 60:
        r_pts = 11
    elif d <= 120:
        r_pts = 6
    else:
        r_pts = 2

    # product fit
    combined = max(fit_info["cmc_score"], fit_info["srw_score"])
    if fit_info["fit"].startswith("Both"):
        f_pts = 25
    elif combined >= 16:
        f_pts = 22
    elif combined >= 11:
        f_pts = 18
    elif combined >= 7:
        f_pts = 14
    elif fit_info["in_scope"]:
        f_pts = 8
    else:
        f_pts = 0

    return _round_half_up(v_pts + r_pts + f_pts)
