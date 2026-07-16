"""FAQ schema generation agent for crawler-facing JSON-LD data."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from blog_manager.config import FAQ_LLM_CONFIG
from blog_manager.services.llm_client import BlogLlmClient

logger = logging.getLogger(__name__)

FAQ_GENERATION_PROMPT = """You are faq_schema_agent, a focused Entourage blog metadata specialist.

MISSION:
Generate concise FAQ items for crawler-facing FAQPage JSON-LD schema.

RESPONSIBILITIES:
- Read the finalized expanded post from the observation.
- Create 2 to 3 FAQ items that answer likely long-tail search questions.
- Base every question and answer on the article content, SEO metadata, and safety boundaries.
- Keep answers concise, accurate, and suitable for structured data.

BOUNDARIES:
- Do not rewrite the article.
- Do not add FAQ content to body_markdown.
- Do not fabricate citations, study details, credentials, medical claims, or guaranteed outcomes.
- Do not decide workflow routing, publishing, retries, or failure handling.

OUTPUT:
Do not add any text before or after the JSON.
Return ONLY valid JSON with exactly this top-level field:
{
  "faq_items": [
    {
      "question": "likely reader/search question",
      "answer": "concise, accurate answer"
    }
  ]
}
"""


class FaqGenerationError(RuntimeError):
    """Raised when the FAQ generation agent cannot produce valid FAQ items."""


class FaqGenerationAgent:
    """Mini agent that creates FAQ items for schema-only publication metadata."""

    def __init__(self, llm_client: BlogLlmClient | None = None):
        self.llm_client = llm_client or BlogLlmClient(config=FAQ_LLM_CONFIG)

    async def generate_faq_items(self, observation: str) -> list[dict[str, str]]:
        """Generate validated FAQ items from a pre-FAQ graph observation."""
        messages = self._build_messages(observation)
        raw_response = await self.llm_client.chat_completion(messages)

        try:
            payload = _parse_llm_json(raw_response)
            return _faq_items_from_payload(payload.get("faq_items"))
        except FaqGenerationError:
            logger.warning("FAQ generation output invalid; retrying with repair prompt")
            repaired_response = await self.llm_client.chat_completion(
                [
                    *messages,
                    {"role": "assistant", "content": raw_response},
                    {
                        "role": "user",
                        "content": (
                            "Repair the previous output. Return only valid JSON matching "
                            'the schema {"faq_items":[{"question":"...","answer":"..."}]}.'
                        ),
                    },
                ]
            )
            payload = _parse_llm_json(repaired_response)
            return _faq_items_from_payload(payload.get("faq_items"))

    def _build_messages(self, observation: str) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": FAQ_GENERATION_PROMPT},
            {"role": "user", "content": observation},
        ]


def _parse_llm_json(raw: str) -> dict[str, Any]:
    text = _clean_response(raw)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as whole_response_error:
        parsed = _parse_embedded_json_object(text)
        if parsed is None:
            raise FaqGenerationError(
                f"FAQ generation JSON parse failed: {whole_response_error}"
            ) from whole_response_error

    if not isinstance(parsed, dict):
        raise FaqGenerationError("FAQ generation output must be a JSON object.")
    return parsed


def _parse_embedded_json_object(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            parsed, _ = decoder.raw_decode(text[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _clean_response(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def _faq_items_from_payload(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise FaqGenerationError("FAQ generation output must include faq_items list.")

    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()
        answer = str(item.get("answer") or "").strip()
        if not question or not answer:
            continue
        key = question.casefold()
        if key in seen:
            continue
        seen.add(key)
        items.append({"question": question, "answer": answer})

    if not items:
        raise FaqGenerationError("FAQ generation output must include at least one valid FAQ item.")
    return items[:2]
