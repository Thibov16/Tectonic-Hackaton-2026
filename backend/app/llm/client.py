"""Optional Anthropic client — explain-only, never scores."""

from __future__ import annotations

import logging
from typing import Any

from app.config import AppConfig
from app.llm.prompts import answer_prompt

logger = logging.getLogger(__name__)


def maybe_phrase_answer(
    verdict: dict[str, Any], query_ctx: dict[str, Any], config: AppConfig
) -> str | None:
    if not config.anthropic_api_key:
        return None
    try:
        import anthropic
    except ImportError:
        logger.warning("anthropic package missing; using fallback phrasing")
        return None

    client = anthropic.Anthropic(api_key=config.anthropic_api_key)
    prompt = answer_prompt(verdict, query_ctx)
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            msg = client.messages.create(
                model=config.claude_model,
                max_tokens=400,
                temperature=0,
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(
                block.text for block in msg.content if getattr(block, "type", "") == "text"
            ).strip()
            if text:
                return text
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            logger.warning("LLM attempt %s failed: %s", attempt + 1, exc)
    logger.warning("LLM fallback used (%s)", last_err)
    return None


def phrase_or_fallback(
    verdict: dict[str, Any], query_ctx: dict[str, Any], config: AppConfig
) -> tuple[str, str]:
    from app.llm.fallback import compose_answer

    phrased = maybe_phrase_answer(verdict, query_ctx, config)
    if phrased:
        return phrased, "llm"
    return compose_answer(verdict, query_ctx), "deterministic"
