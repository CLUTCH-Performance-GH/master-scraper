"""Central settings. All keys come from .env — never hardcode keys in source files."""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# ---- API keys -------------------------------------------------------------
SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")
FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")  # SDK also reads this itself
# Optional — OpenCorporates' free tier works unauthenticated but rate-limits
# hard; a free API token raises the ceiling. Leave blank to run without one.
OPENCORPORATES_API_TOKEN = os.getenv("OPENCORPORATES_API_TOKEN", "")

# ---- Cost ladder budgets ---------------------------------------------------
# The whole framework is built around: cache -> free HTTP -> Serper -> Firecrawl -> LLM.
SERPER_RATE_LIMIT_SECONDS = float(os.getenv("MSC_SERPER_RATE", "0.4"))
FIRECRAWL_RATE_LIMIT_SECONDS = float(os.getenv("MSC_FIRECRAWL_RATE", "0.6"))
FIRECRAWL_MIN_REMAINING_CREDITS = int(os.getenv("MSC_FIRECRAWL_FLOOR", "200"))
FIRECRAWL_RUN_BUDGET = int(os.getenv("MSC_FIRECRAWL_RUN_BUDGET", "500"))
SERPER_RUN_BUDGET = int(os.getenv("MSC_SERPER_RUN_BUDGET", "5000"))

# ---- HTTP ------------------------------------------------------------------
HTTP_TIMEOUT = 30
HTTP_MAX_RETRIES = 3
HTTP_RATE_LIMIT_SECONDS = float(os.getenv("MSC_HTTP_RATE", "0.4"))
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
HTTP_MAX_WORKERS = int(os.getenv("MSC_HTTP_WORKERS", "10"))

# ---- Cache -----------------------------------------------------------------
CACHE_DB = PROJECT_ROOT / "data" / "cache.sqlite3"
DEFAULT_CACHE_TTL_DAYS = 14  # re-fetch pages older than this; Serper results cached 30 days

# ---- LLM -------------------------------------------------------------------
# Opus 4.8 for synthesis/judgment; set MSC_LLM_FAST_MODEL=claude-haiku-4-5 for
# high-volume field extraction where cost matters more than nuance.
LLM_MODEL = os.getenv("MSC_LLM_MODEL", "claude-opus-4-8")
LLM_FAST_MODEL = os.getenv("MSC_LLM_FAST_MODEL", "claude-haiku-4-5")
LLM_MAX_TOKENS = 8000

# ---- Output ----------------------------------------------------------------
OUTPUT_DIR = PROJECT_ROOT / "output"
CHECKPOINT_DIR = PROJECT_ROOT / "data" / "checkpoints"
CLUTCH_BLUE = "#2F6DB3"

for _d in (CACHE_DB.parent, OUTPUT_DIR, CHECKPOINT_DIR):
    _d.mkdir(parents=True, exist_ok=True)
