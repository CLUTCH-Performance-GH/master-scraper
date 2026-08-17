"""Claude layer — the piece none of the three old pipelines had.

What it replaces:
  - 130 hand-maintained keyword variants (BASF config.py)  -> semantic judgment
  - regex tagline/benefit extraction that broke per-site    -> schema extraction
  - rule-based "data-driven vs grower-centric" tone buckets -> real analysis
  - name/title regexes that missed half the About pages     -> contact extraction

Cost controls baked in:
  - every call cached in SQLite (same page never analyzed twice)
  - structured outputs (output_config.format) so responses are valid JSON, no retries
  - prompt caching on the system prompt (~90% cheaper on repeated context)
  - batch_extract() routes bulk jobs through the Batches API at 50% off
  - effort='low' for extraction; full effort reserved for synthesis
"""

import json
import logging
import time
from typing import Any, Optional

import anthropic

from . import cache, settings

log = logging.getLogger("msc.llm")


# Models observed to reject output_config.effort. Populated at runtime on the
# first rejection so a new model is handled without a code change.
_NO_EFFORT_MODELS: set[str] = set()


class Claude:
    def __init__(self, model: str = ""):
        self.client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
        self.model = model or settings.LLM_MODEL
        self.fast_model = settings.LLM_FAST_MODEL
        self.input_tokens = 0
        self.output_tokens = 0

    # -- single structured extraction ---------------------------------------

    def extract(self, prompt: str, schema: dict, system: str = "",
                model: str = "", effort: str = "low",
                max_tokens: int = settings.LLM_MAX_TOKENS) -> Optional[dict]:
        """One structured-output call, cached. Returns the parsed JSON object."""
        mdl = model or self.model
        cached = cache.get("llm", mdl, system, prompt, schema)
        if cached is not None:
            return cached

        kwargs: dict[str, Any] = {
            "model": mdl,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
            "output_config": {
                "format": {"type": "json_schema", "schema": schema},
            },
        }
        # `effort` is not accepted by every model (Haiku 4.5 rejects it outright).
        # Send it only where it is known to work, and learn the answer from the
        # API rather than hardcoding a model list that will go stale.
        if effort and mdl not in _NO_EFFORT_MODELS:
            kwargs["output_config"]["effort"] = effort
        if system:
            # cache_control: identical system prompts across hundreds of pages
            # are served from Anthropic's prompt cache at ~0.1x input price
            kwargs["system"] = [{"type": "text", "text": system,
                                 "cache_control": {"type": "ephemeral"}}]
        try:
            resp = self.client.messages.create(**kwargs)
        except anthropic.APIError as exc:
            if "effort" in str(exc) and "output_config" in kwargs:
                _NO_EFFORT_MODELS.add(mdl)
                kwargs["output_config"].pop("effort", None)
                try:
                    resp = self.client.messages.create(**kwargs)
                except anthropic.APIError as exc2:
                    log.warning("llm extract failed: %s", exc2)
                    return None
            else:
                log.warning("llm extract failed: %s", exc)
                return None

        self.input_tokens += resp.usage.input_tokens
        self.output_tokens += resp.usage.output_tokens
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            log.warning("llm returned non-JSON despite schema")
            return None
        cache.put("llm", mdl, system, prompt, schema, value=data)
        return data

    # -- bulk extraction at 50% cost via the Batches API ---------------------

    def batch_extract(self, items: list[tuple[str, str]], schema: dict,
                      system: str = "", model: str = "",
                      poll_seconds: int = 30) -> dict[str, Optional[dict]]:
        """items = [(custom_id, prompt), ...]. Returns {custom_id: parsed_json}.

        Half-price, completes within ~1 hour for typical sizes. Use this for
        anything over ~50 pages where you don't need answers immediately.
        Cached items are answered locally and excluded from the batch.
        """
        mdl = model or self.fast_model
        results: dict[str, Optional[dict]] = {}
        pending = []
        for cid, prompt in items:
            hit = cache.get("llm", mdl, system, prompt, schema)
            if hit is not None:
                results[cid] = hit
            else:
                pending.append((cid, prompt))
        if not pending:
            return results

        sys_blocks = ([{"type": "text", "text": system,
                        "cache_control": {"type": "ephemeral"}}] if system else None)
        requests = []
        for cid, prompt in pending:
            params: dict[str, Any] = {
                "model": mdl,
                "max_tokens": settings.LLM_MAX_TOKENS,
                "messages": [{"role": "user", "content": prompt}],
                "output_config": {"format": {"type": "json_schema", "schema": schema}},
            }
            if sys_blocks:
                params["system"] = sys_blocks
            requests.append({"custom_id": cid, "params": params})

        batch = self.client.messages.batches.create(requests=requests)
        log.info("submitted batch %s (%d requests)", batch.id, len(requests))

        while True:
            batch = self.client.messages.batches.retrieve(batch.id)
            if batch.processing_status == "ended":
                break
            time.sleep(poll_seconds)

        prompt_by_id = dict(pending)
        for result in self.client.messages.batches.results(batch.id):
            cid = result.custom_id
            if result.result.type == "succeeded":
                msg = result.result.message
                text = next((b.text for b in msg.content if b.type == "text"), "")
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    data = None
                results[cid] = data
                if data is not None and cid in prompt_by_id:
                    cache.put("llm", mdl, system, prompt_by_id[cid], schema, value=data)
            else:
                log.warning("batch item %s: %s", cid, result.result.type)
                results[cid] = None
        return results

    # -- prose synthesis (reports, threat assessments) ------------------------

    def synthesize(self, prompt: str, system: str = "", max_tokens: int = 16000) -> str:
        """Full-effort prose generation with adaptive thinking — for the
        'so what' competitive assessments the BASF rule engine faked."""
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "thinking": {"type": "adaptive"},
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = [{"type": "text", "text": system,
                                 "cache_control": {"type": "ephemeral"}}]
        with self.client.messages.stream(**kwargs) as stream:
            msg = stream.get_final_message()
        self.input_tokens += msg.usage.input_tokens
        self.output_tokens += msg.usage.output_tokens
        return next((b.text for b in msg.content if b.type == "text"), "")


# ---------------------------------------------------------------------------
# Ready-made schemas for the two recurring jobs
# ---------------------------------------------------------------------------

CONTACT_SCHEMA = {
    "type": "object",
    "properties": {
        "contacts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "title": {"type": "string"},
                    "email": {"type": ["string", "null"]},
                    "phone": {"type": ["string", "null"]},
                    "is_decision_maker": {"type": "boolean"},
                },
                "required": ["name", "title", "email", "phone", "is_decision_maker"],
                "additionalProperties": False,
            },
        },
        "company_phone": {"type": ["string", "null"]},
        "company_email": {"type": ["string", "null"]},
    },
    "required": ["contacts", "company_phone", "company_email"],
    "additionalProperties": False,
}

MESSAGING_SCHEMA = {
    "type": "object",
    "properties": {
        "hero_tagline": {"type": ["string", "null"]},
        "value_proposition": {"type": ["string", "null"]},
        "benefit_claims": {"type": "array", "items": {"type": "string"}},
        "performance_claims": {"type": "array", "items": {"type": "string"}},
        "differentiation_language": {"type": "array", "items": {"type": "string"}},
        "tone": {"type": "string",
                 "enum": ["data-driven", "grower-centric", "innovation-focused",
                          "value-focused", "mixed", "unclear"]},
        "keyword_overlap": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string"},
                    "present": {"type": "boolean"},
                    "evidence_quote": {"type": ["string", "null"]},
                },
                "required": ["keyword", "present", "evidence_quote"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["hero_tagline", "value_proposition", "benefit_claims",
                 "performance_claims", "differentiation_language", "tone",
                 "keyword_overlap"],
    "additionalProperties": False,
}
