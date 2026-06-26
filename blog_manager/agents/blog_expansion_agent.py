"""Blog content expansion agent prompt and parsing."""

from __future__ import annotations

import json
import logging
import random
import re
from datetime import date
from typing import Any, NamedTuple

from blog_manager.config import EXPANSION_LLM_CONFIG
from blog_manager.schemas import (
    BlogAgentResult,
    BlogIdea,
    ExpandedPost,
    SupportingImage,
)
from blog_manager.services.idea_parser import slugify
from blog_manager.services.llm_client import BlogLlmClient

logger = logging.getLogger(__name__)

BLOG_CATEGORIES = [
    "Purpose",
    "Stoicism",
    "Relationships",
    "Productivity",
    "Habits",
    "Inner Work",
    "Love",
    "Philosophy",
    "Unknown",
]
DEFAULT_CATEGORY = "Unknown"
SEARCH_INTENTS = [
    "informational",
    "problem_solving",
    "comparative",
    "transactional",
    "unknown",
]
DEFAULT_SEARCH_INTENT = "unknown"
LLM_JSON_LOG_LIMIT = 12000


class StyleOption(NamedTuple):
    """Short runtime prompt fragment for article variety."""

    name: str
    instruction: str


class BlogStyleProfile(NamedTuple):
    """Selected voice notes for one expansion request."""

    persona: StyleOption
    rhetorical_shape: StyleOption
    opening_constraint: StyleOption


_STYLE_RANDOM = random.SystemRandom()

PERSONAS = [
    StyleOption(
        "practical coach",
        "Be clear, grounded, and action-oriented with gentle encouragement.",
    ),
    StyleOption(
        "reflective philosopher",
        "Use thoughtful meaning-making without drifting into abstraction.",
    ),
    StyleOption(
        "playful friend",
        "Add light humor and warmth while staying emotionally safe.",
    ),
    StyleOption(
        "lyrical guide",
        "Use vivid but simple imagery, then return quickly to practical value.",
    ),
    StyleOption(
        "direct field guide",
        "Be concise, concrete, and useful; avoid ornamental phrasing.",
    ),
]

RHETORICAL_SHAPES = [
    StyleOption(
        "list-guided",
        "Propose a clear framework with titled concepts, principles and actionable strategies.",
    ),
    StyleOption(
        "question-guided",
        "Use open-ended questions followed by structured answers to build momentum.",
    ),
    StyleOption(
        "reflective essay",
        "Move through observation, analysis, insights/epiphanies/takeaways.",
    ),
    StyleOption(
        "practical framework",
        "Define the problem, offer a simple model, and practical steps to take.",
    ),
]

OPENING_CONSTRAINTS = [
    StyleOption("concrete scene", "Begin with a concrete everyday scene."),
    StyleOption("common misconception", "Begin by correcting a common misconception."),
    StyleOption("tiny story", "Begin with a tiny story in 2 to 3 sentences."),
    StyleOption("hopeful promise", "Begin with a hopeful promise to the reader."),
]

SYSTEM_PROMPT = """You are BlogExpansionAgent, the Entourage blog content specialist.

ROLE:
- Expand rough Markdown blog ideas into complete, engaging Entourage blog posts.
- Use a warm, practical, emotionally grounded tone.
- Preserver the user's intent 
- Avoid medical diagnosis, guaranteed outcomes, or treatment claims.

CONTENT RESPONSIBILITIES:
- Write publication-ready Markdown with a concise excerpt, strong title, useful headings (different from title), short paragraphs, and a grounded closing reflection.
- Build each article around a clear search intent (informational|problem-solving|comparative|transactional|unknown). Infer from user's idea.
- Give readers a direct answer, definition, or practical framing in the first 100 words.
- Use plenty of emoticons at both mid and end of sentences 
- Limit the post length to between 700 words and 900 words.

IMPORTANT INSTRUCTIONS:
- Keep `seo_title` concise and search-friendly at 40 to 50 characters.
- Keep `seo_description` compelling and accurate at 120 to 150 characters.
- Provide a high-level `image_prompt` describing the desired cover mood and subject.
- Add 1 to 2 supporting image placeholders as full-line JPEG markers like `{image_001.jpg}` in `body_markdown`.
- For every supporting image placeholder, add one matching `supporting_images` item with filename, prompt, and alt_text.
- Choose one `category` for topical authority from: Purpose|Stoicism|Relationships|Productivity|Habits|Inner Work|Love|Philosophy|Unknown.
- Pick one long-tail `primary_keyword`.
a) Use the primary keyword naturally in the opening paragraph, `seo_title`, and `seo_description`.
b) Add 2 to 4 related short keywords to `tags` to help readers search for relevant articles.

GOOD TO HAVE:
- Include optional `safety_notes` for any claims or wording that should remain cautious. Omit the field if there are no useful notes.
- Include optional `citation_suggestions` when relevant, such as credible books, researchers, or studies. Omit the field if there are no useful suggestions. 

BOUNDARIES:
- Do not fabricate citations, URLs, people's names, study details, credentials.
- Do not add safety notes or citation suggestions to `body_markdown`. Only include them as JSON fields.
- Do not decide workflow routing, publishing, retries, or failure handling.
- Do not perform S3 operations.
- Do not render HTML or generate images.

OUTPUT:
Do not add any text before or after the JSON.
Return ONLY valid JSON with exactly these top-level fields:
{
  "title": "string",
  "slug": "kebab-case-string",
  "date": "YYYY-MM-DD",
  "excerpt": "string",
  "body_markdown": "string",
  "image_prompt": "string",
  "supporting_images": [
    {
      "filename": "image_001.jpg",
      "prompt": "specific supporting image generation prompt",
      "alt_text": "accessible image description"
    }
  ],
  "tags": ["short keyword"],
  "category": "Purpose|Stoicism|Relationships|Productivity|Habits|Inner Work|Love|Philosophy|Unknown",
  "seo_title": "string",
  "seo_description": "string",
  "primary_keyword": "long-tail keyword string",
  "search_intent": "informational|problem_solving|comparative|transactional|unknown",
  "citation_suggestions": ["credible source to consider"],
  "safety_notes": ["string"]
}
"""


class BlogExpansionError(RuntimeError):
    """Raised when the main expansion agent cannot produce valid output."""


class BlogExpansionAgent:
    """Content agent that expands ideas and revises expanded posts."""

    def __init__(self, llm_client: BlogLlmClient | None = None):
        self.llm_client = llm_client or BlogLlmClient(config=EXPANSION_LLM_CONFIG)

    async def expand_idea(
        self,
        idea: BlogIdea,
    ) -> BlogAgentResult:
        """Expand one parsed idea into a structured post."""
        style_profile = _select_style_profile()
        logger.info(
            "Selected blog expansion style persona=%s rhetorical_shape=%s opening=%s",
            style_profile.persona.name,
            style_profile.rhetorical_shape.name,
            style_profile.opening_constraint.name,
        )
        messages = self._build_messages(
            _build_expansion_user_prompt(idea, style_profile)
        )
        raw_response = await self.llm_client.chat_completion(messages)

        try:
            parsed = _parse_llm_json(raw_response)
            post = _expanded_post_from_payload(parsed)
        except BlogExpansionError:
            logger.warning("Main expansion output invalid; retrying with repair prompt")
            repaired_response = await self.llm_client.chat_completion(
                [
                    *messages,
                    {"role": "assistant", "content": raw_response},
                    {
                        "role": "user",
                        "content": "Repair the previous output. Return only valid JSON matching the required schema.",
                    },
                ]
            )
            parsed = _parse_llm_json(repaired_response)
            post = _expanded_post_from_payload(parsed)
            raw_response = repaired_response

        return BlogAgentResult(
            post=post,
            raw_response=raw_response,
        )

    async def revise_content(
        self,
        post: ExpandedPost,
        *,
        revision_instruction: str,
    ) -> BlogAgentResult:
        """Revise the current expanded post using supervisor instructions."""
        messages = self._build_messages(
            _build_revision_user_prompt(
                post,
                revision_instruction=revision_instruction,
            )
        )
        raw_response = await self.llm_client.chat_completion(messages)

        try:
            parsed = _parse_llm_json(raw_response)
            revised_post = _expanded_post_from_payload(parsed)
        except BlogExpansionError:
            logger.warning("Content revision output invalid; retrying with repair prompt")
            repaired_response = await self.llm_client.chat_completion(
                [
                    *messages,
                    {"role": "assistant", "content": raw_response},
                    {
                        "role": "user",
                        "content": "Repair the previous output. Return only valid JSON matching the required schema.",
                    },
                ]
            )
            parsed = _parse_llm_json(repaired_response)
            revised_post = _expanded_post_from_payload(parsed)
            raw_response = repaired_response

        return BlogAgentResult(post=revised_post, raw_response=raw_response)

    def _build_messages(
        self,
        user_prompt: str,
    ) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]


def _select_style_profile() -> BlogStyleProfile:
    return BlogStyleProfile(
        persona=_STYLE_RANDOM.choice(PERSONAS),
        rhetorical_shape=_STYLE_RANDOM.choice(RHETORICAL_SHAPES),
        opening_constraint=_STYLE_RANDOM.choice(OPENING_CONSTRAINTS),
    )


def _style_profile_prompt(style_profile: BlogStyleProfile) -> str:
    return f"""## Runtime voice notes
These are light style nudges only.
Follow all system, safety, SEO, image, and JSON schema instructions first.
- Persona: {style_profile.persona.name}. {style_profile.persona.instruction}
- Shape: {style_profile.rhetorical_shape.name}. {style_profile.rhetorical_shape.instruction}
- Opening: {style_profile.opening_constraint.name}. {style_profile.opening_constraint.instruction}
"""


def _build_expansion_user_prompt(
    idea: BlogIdea,
    style_profile: BlogStyleProfile,
) -> str:
    frontmatter = json.dumps(idea.frontmatter, indent=2, ensure_ascii=False)
    style_notes = _style_profile_prompt(style_profile)
    return f"""{style_notes}

## Idea source
S3 key: {idea.key}

## Frontmatter
{frontmatter}

## Rough content
{idea.body.strip()}
"""


def _build_revision_user_prompt(
    post: ExpandedPost,
    *,
    revision_instruction: str,
) -> str:
    post_payload = {
        "title": post.title,
        "slug": post.slug,
        "date": post.date,
        "excerpt": post.excerpt,
        "body_markdown": post.body_markdown,
        "image_prompt": post.image_prompt,
        "supporting_images": [
            {
                "filename": image.filename,
                "prompt": image.prompt,
                "alt_text": image.alt_text,
            }
            for image in post.supporting_images
        ],
        "tags": post.tags,
        "category": post.category,
        "seo_title": post.seo_title,
        "seo_description": post.seo_description,
        "primary_keyword": post.primary_keyword,
        "search_intent": post.search_intent,
        "citation_suggestions": post.citation_suggestions,
        "safety_notes": post.safety_notes,
    }
    return f"""## Current expanded post
{json.dumps(post_payload, indent=2, ensure_ascii=False)}

## Revision instruction
{revision_instruction.strip()}

Preserve the current article's voice, structure, and opening style.
Change direction only if the revision instruction explicitly asks for it.
"""


def _parse_llm_json(raw: str) -> dict[str, Any]:
    text = _clean_response(raw)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as whole_response_error:
        parsed = _parse_embedded_json_object(text)
        if parsed is None:
            _log_invalid_llm_json_response(raw, text, whole_response_error)
            raise BlogExpansionError(
                f"Expansion JSON parse failed: {whole_response_error}"
            ) from whole_response_error

    if not isinstance(parsed, dict):
        _log_invalid_llm_json_response(raw, text)
        raise BlogExpansionError("Expansion output must be a JSON object.")
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


def _log_invalid_llm_json_response(
    raw: str,
    cleaned: str,
    exc: json.JSONDecodeError | None = None,
) -> None:
    raw_preview = _truncate_for_log(raw or "")
    cleaned_preview = _truncate_for_log(cleaned)
    reason = f" error={exc}" if exc else ""
    message = (
        "BlogExpansionAgent invalid JSON response"
        f"{reason} raw_len={len(raw or '')} cleaned_len={len(cleaned)} "
        f"raw_response={raw_preview!r} cleaned_response={cleaned_preview!r}"
    )
    print(message)
    logger.warning(message)


def _truncate_for_log(value: str) -> str:
    if len(value) <= LLM_JSON_LOG_LIMIT:
        return value
    return (
        value[:LLM_JSON_LOG_LIMIT]
        + f"... [truncated {len(value) - LLM_JSON_LOG_LIMIT} chars]"
    )


def _clean_response(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def _expanded_post_from_payload(payload: dict[str, Any]) -> ExpandedPost:
    title = _required_string(payload, "title")
    slug = slugify(str(payload.get("slug") or title))
    post_date = str(payload.get("date") or date.today().isoformat()).strip()
    excerpt = _required_string(payload, "excerpt")
    body_markdown = _required_string(payload, "body_markdown")
    image_prompt = _required_string(payload, "image_prompt")
    supporting_images = _supporting_images_from_payload(payload.get("supporting_images"), body_markdown)
    tags = _tags_from_payload(payload.get("tags"), payload.get("secondary_keywords"))
    category = _category_from_payload(payload.get("category"))
    search_intent = _search_intent_from_payload(payload.get("search_intent"))

    return ExpandedPost(
        title=title,
        slug=slug,
        date=post_date,
        excerpt=excerpt,
        body_markdown=body_markdown,
        image_prompt=image_prompt,
        seo_title=str(payload.get("seo_title") or title).strip(),
        seo_description=str(payload.get("seo_description") or excerpt).strip(),
        primary_keyword=str(payload.get("primary_keyword") or "").strip(),
        search_intent=search_intent,
        faq_items=_faq_items_from_payload(payload.get("faq_items")),
        citation_suggestions=_deduped_string_list(payload.get("citation_suggestions")),
        safety_notes=_string_list(payload.get("safety_notes")),
        supporting_images=supporting_images,
        tags=tags,
        category=category,
    )


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise BlogExpansionError(f"Expansion output missing required field: {key}")
    return value


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _deduped_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    seen: set[str] = set()
    for item in _string_list(value):
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        items.append(item)
    return items


def _tags_from_payload(value: Any, legacy_secondary_keywords: Any = None) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    for item in [*_string_list(value), *_string_list(legacy_secondary_keywords)]:
        tag = str(item).strip()
        if not tag:
            continue
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        tags.append(tag)
    return tags


def _category_from_payload(value: Any) -> str:
    category = str(value or "").strip()
    if category in BLOG_CATEGORIES:
        return category
    return DEFAULT_CATEGORY


def _search_intent_from_payload(value: Any) -> str:
    search_intent = str(value or "").strip()
    if search_intent in SEARCH_INTENTS:
        return search_intent
    return DEFAULT_SEARCH_INTENT


def _faq_items_from_payload(value: Any) -> list[dict[str, str]]:
    return []


def _supporting_images_from_payload(value: Any, body_markdown: str) -> list[SupportingImage]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise BlogExpansionError("supporting_images must be a list.")

    images: list[SupportingImage] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise BlogExpansionError("supporting_images items must be objects.")
        filename = str(item.get("filename") or "").strip()
        prompt = str(item.get("prompt") or "").strip()
        alt_text = str(item.get("alt_text") or "").strip()
        if not re.fullmatch(r"image_\d{3}\.jpg", filename):
            raise BlogExpansionError(
                "supporting_images filenames must look like image_001.jpg."
            )
        if filename in seen:
            raise BlogExpansionError(f"Duplicate supporting image filename: {filename}")
        if not prompt:
            raise BlogExpansionError(f"Supporting image {filename} is missing prompt.")
        if not alt_text:
            raise BlogExpansionError(f"Supporting image {filename} is missing alt_text.")
        placeholder = "{" + filename + "}"
        count = len(re.findall(rf"(?m)^\s*{re.escape(placeholder)}\s*$", body_markdown))
        if count != 1:
            raise BlogExpansionError(
                f"Supporting image {filename} must appear exactly once as a full-line placeholder."
            )
        seen.add(filename)
        images.append(SupportingImage(filename=filename, prompt=prompt, alt_text=alt_text))
    return images
