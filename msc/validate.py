"""Validation at scrape time, not as version 11 of a fix script.

This module is the distilled lesson of the BASF project's 15 versions:
  - PDF binary / garbage text detection  (deep_clean_v2 lesson)
  - fake-page detection                  (validate_v12 lesson — sponsor/speaker pages)
  - common-word proximity matching       (validate_v12 lesson — "Gem", "Procure")
  - cross-company contamination check    (validate_v12 lesson)
plus the Ground Truth Protocol audit from the CLS project. Every record gets
validated the moment it is scraped, so no post-hoc cleanup pass is needed.
"""

import re
from collections import Counter
from dataclasses import dataclass, field

from .extract import addr_key

# ---------------------------------------------------------------------------
# Garbage / binary detection
# ---------------------------------------------------------------------------

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def is_garbage_text(text: str) -> bool:
    """True for PDF binary spills, encoding disasters, control-char soup."""
    if not text:
        return False
    if text.lstrip().startswith("%PDF"):
        return True
    sample = text[:4000]
    if _CONTROL_RE.search(sample):
        return True
    words = re.findall(r"[A-Za-z]{2,}", sample)
    return (sum(len(w) for w in words) / max(len(sample), 1)) < 0.30


def clean_text(text: str) -> str:
    """ASCII-safe cleanup (xlsxwriter/python-docx both choke on weird bytes)."""
    replacements = {"‘": "'", "’": "'", "“": '"', "”": '"',
                    "–": "-", "—": "-", "®": "(R)", "™": "(TM)",
                    " ": " ", "…": "..."}
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    text = _CONTROL_RE.sub("", text)
    return text.encode("ascii", "ignore").decode("ascii")


# ---------------------------------------------------------------------------
# Page-quality + mention verification
# ---------------------------------------------------------------------------

_FAKE_PAGE_MARKERS = ("sponsor", "speakers", "speaker list", "exhibitor", "booth",
                      "conference agenda", "podcast archive", "category archive",
                      "tag archive", "back issues", "media kit", "advertise with")


def is_fake_page(text: str, url: str = "") -> bool:
    """Sponsor pages, speaker lists, archives — pages that mention everything
    and say nothing. These produced most of the bad URLs in BASF V10."""
    lower = (text or "").lower()[:6000]
    hits = sum(1 for m in _FAKE_PAGE_MARKERS if m in lower)
    url_hit = any(s in (url or "").lower()
                  for s in ("/sponsor", "/speakers", "/exhibitors", "/category/", "/tag/"))
    return hits >= 2 or url_hit


def verify_mention(text: str, entity: str, company: str = "",
                   common_word: bool = False, proximity_words: int = 60) -> bool:
    """Does this page genuinely talk about `entity`?

    For entities whose name is an ordinary English word ("Gem", "Inspire"),
    require the company name within ~proximity_words words, or an explicit
    qualifier ("gem fungicide"). This single function replaces validate_v11
    and validate_v12.
    """
    if not text or not entity:
        return False
    text_lower, ent = text.lower(), entity.lower()
    if ent not in text_lower:
        return False
    if not common_word:
        return True
    if re.search(rf"\b{re.escape(ent)}\W+(fungicide|herbicide|insecticide|brand)\b", text_lower):
        return True
    if not company:
        return False
    words = text_lower.split()
    ent_idx = [i for i, w in enumerate(words) if ent in w]
    co = company.lower().split()[0]
    co_idx = [i for i, w in enumerate(words) if co in w]
    if not (ent_idx and co_idx):
        return False
    return min(abs(e - c) for e in ent_idx for c in co_idx) <= proximity_words


def detect_contamination(text: str, own_company: str, competitors: list[str]) -> str:
    """Return the competitor name when a page is mostly about someone else."""
    lower = (text or "").lower()
    own_n = lower.count(own_company.lower())
    for rival in competitors:
        if rival.lower() == own_company.lower():
            continue
        if lower.count(rival.lower()) > max(own_n * 2, 3):
            return rival
    return ""


# ---------------------------------------------------------------------------
# Ground Truth Protocol — flag, don't drop; audit every tab
# ---------------------------------------------------------------------------

@dataclass
class GTPCheck:
    tab: str
    source_url: str
    expected: int
    actual: int
    missing_key_field: int
    co_located: int
    notes: str = ""

    @property
    def verdict(self) -> str:
        if self.actual >= self.expected and (
                self.actual == 0 or self.missing_key_field / self.actual < 0.05):
            return "PASS"
        return "REVIEW"


@dataclass
class GTPAudit:
    checks: list[GTPCheck] = field(default_factory=list)

    def check(self, tab: str, source_url: str, expected: int, rows: list[dict],
              key_field: str = "street",
              addr_fields: tuple = ("street", "city", "state", "zip"),
              notes: str = "") -> GTPCheck:
        missing = sum(1 for r in rows if not str(r.get(key_field, "")).strip())
        keys = [addr_key(*(str(r.get(f, "")) for f in addr_fields)) for r in rows]
        counts = Counter(k for k in keys if k.replace("|", "").strip())
        co_located = sum(1 for k in keys if counts.get(k, 0) > 1)
        c = GTPCheck(tab, source_url, expected, len(rows), missing, co_located, notes)
        self.checks.append(c)
        return c

    def flag_colocated(self, rows: list[dict],
                       addr_fields: tuple = ("street", "city", "state", "zip")) -> None:
        """Mutates rows: adds 'co_location_flag'. Duplicates are real (branch +
        terminal + seed hub at one address) — flag them, never drop them."""
        keys = [addr_key(*(str(r.get(f, "")) for f in addr_fields)) for r in rows]
        counts = Counter(k for k in keys if k.replace("|", "").strip())
        for row, key in zip(rows, keys):
            n = counts.get(key, 0)
            row["co_location_flag"] = f"Co-located w/ {n - 1} other(s)" if n > 1 else ""

    def summary(self) -> str:
        lines = [f"{c.verdict:6} {c.tab}: expected={c.expected} actual={c.actual} "
                 f"missing_{'key'}={c.missing_key_field} colocated={c.co_located}"
                 for c in self.checks]
        return "\n".join(lines)
