"""Pipeline runner: declarative sources, parallel fetching, checkpoint/resume.

A Source declares HOW to discover items (sitemap, serper queries, locator API,
url list) and how to parse one item into rows. The runner handles everything
the three old projects each re-implemented: checkpointing, retries, parallel
fetching, validation, dedup, and the GTP audit.
"""

import concurrent.futures as cf
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from . import settings
from .dedupe import merge_rows
from .net import Firecrawl, Http, Serper, fetch_smart
from .validate import GTPAudit, clean_text, is_garbage_text

log = logging.getLogger("msc.pipeline")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")


@dataclass
class Source:
    """One data source. Provide discover() and parse(); the runner does the rest.

    discover(ctx) -> list of item dicts, each with at least {"url": ...}
                     (or arbitrary payloads if parse doesn't need a fetch)
    parse(item, text, ctx) -> list of row dicts
    """
    name: str
    audience: str = ""
    source_url: str = ""           # canonical URL for the GTP sheet
    expected_count: int = 0        # GTP expected count (0 = unknown)
    discover: Callable = None
    parse: Callable = None
    force_render: bool = False     # skip straight to Firecrawl (JS-only sites)
    fetch_items: bool = True       # False when discover() already returns full data
    notes: str = ""


@dataclass
class Context:
    """Shared clients passed to every discover/parse call."""
    http: Http = field(default_factory=Http)
    serper: Serper = field(default_factory=Serper)
    firecrawl: Firecrawl = field(default_factory=Firecrawl)
    claude: Optional[object] = None  # msc.llm.Claude, attach when needed


class Runner:
    def __init__(self, run_name: str, ctx: Optional[Context] = None):
        self.run_name = run_name
        self.ctx = ctx or Context()
        self.audit = GTPAudit()
        self.checkpoint_path = settings.CHECKPOINT_DIR / f"{run_name}.json"
        self.state = self._load_checkpoint()

    # -- checkpointing --------------------------------------------------------

    def _load_checkpoint(self) -> dict:
        if self.checkpoint_path.exists():
            return json.loads(self.checkpoint_path.read_text())
        return {"completed_sources": {}, "started_at": datetime.now(timezone.utc).isoformat()}

    def _save_checkpoint(self) -> None:
        self.checkpoint_path.write_text(json.dumps(self.state, indent=1, default=str))

    # -- run one source --------------------------------------------------------

    def run_source(self, src: Source) -> list[dict]:
        if src.name in self.state["completed_sources"]:
            rows = self.state["completed_sources"][src.name]
            log.info("[%s] resumed from checkpoint: %d rows", src.name, len(rows))
            self._gtp(src, rows)
            return rows

        log.info("[%s] discovering items...", src.name)
        items = src.discover(self.ctx) if src.discover else []
        log.info("[%s] %d items discovered", src.name, len(items))

        rows: list[dict] = []
        if src.fetch_items:
            def worker(item: dict) -> list[dict]:
                url = item.get("url", "")
                method, text = fetch_smart(url, self.ctx.http,
                                           firecrawl=self.ctx.firecrawl,
                                           force_render=src.force_render)
                if method == "failed" or is_garbage_text(text):
                    log.warning("[%s] failed/garbage: %s", src.name, url)
                    return []
                return src.parse(item, clean_text(text), self.ctx) or []

            with cf.ThreadPoolExecutor(max_workers=settings.HTTP_MAX_WORKERS) as pool:
                for i, result in enumerate(pool.map(worker, items), 1):
                    rows.extend(result)
                    if i % 25 == 0:
                        log.info("[%s] %d/%d items", src.name, i, len(items))
        else:
            for item in items:
                rows.extend(src.parse(item, "", self.ctx) or [])

        stamp = datetime.now(timezone.utc).isoformat()
        for r in rows:
            r.setdefault("source", src.name)
            r.setdefault("audience", src.audience)
            r.setdefault("scraped_at", stamp)

        self.state["completed_sources"][src.name] = rows
        self._save_checkpoint()
        self._gtp(src, rows)
        log.info("[%s] done: %d rows", src.name, len(rows))
        return rows

    def _gtp(self, src: Source, rows: list[dict]) -> None:
        expected = src.expected_count or len(rows)
        self.audit.check(src.name, src.source_url, expected, rows, notes=src.notes)

    # -- run everything ---------------------------------------------------------

    def run(self, sources: list[Source], dedupe: bool = True) -> list[dict]:
        all_rows: list[dict] = []
        for src in sources:
            try:
                all_rows.extend(self.run_source(src))
            except Exception:
                log.exception("[%s] source failed — continuing with the rest", src.name)
        if dedupe:
            before = len(all_rows)
            all_rows = merge_rows(all_rows)
            log.info("dedupe: %d -> %d rows", before, len(all_rows))
        self.audit.flag_colocated(all_rows)
        log.info("GTP summary:\n%s", self.audit.summary())
        log.info("spend: serper=%d queries, firecrawl=%d credits",
                 self.ctx.serper.queries_spent, self.ctx.firecrawl.credits_spent)
        return all_rows
