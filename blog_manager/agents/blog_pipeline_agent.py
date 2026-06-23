"""ReAct-style supervisor for the blog generation graph."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from blog_manager.config import PIPELINE_LLM_CONFIG
from blog_manager.schemas import BlogGraphState, BlogPipelineDecision
from blog_manager.services.llm_client import BlogLlmClient

logger = logging.getLogger(__name__)

PIPELINE_SYSTEM_PROMPT = """You are BlogPipelineAgent, the ReAct-style supervisor for the Entourage blog publisher graph.

ROLE:
- Observe the current graph state.
- Reason privately about the next safest workflow action.
- Return exactly one strict JSON decision.

BOUNDARIES:
- Do not write blog content yourself; when content must be expanded or revised,
output decision to expand_content or revise_content with instructions to hand off to BlogExpansionAgent.
- Do not render HTML, generate images, or invoke subagents/tools.
- Do not perform S3 operations. Publisher graph nodes own S3 writes.
- Do not return chain-of-thought. Return a concise reason only.

ALLOWED DECISIONS:
- expand_content: use when no expanded post exists yet.
- revise_content: use when the expanded post exists but needs content revision.
- generate_artifacts: use when content is good enough and local HTML/image artifacts should be produced.
- retry_artifacts: use when artifacts failed validation but retry budget remains.
- publish: use only when content and artifacts are valid.
- fail: use when the workflow cannot safely continue.

OUTPUT:
Return ONLY valid JSON with this schema:
{
  "decision": "expand_content|revise_content|generate_artifacts|retry_artifacts|publish|fail",
  "reason": "short user-safe explanation",
  "content_revision_instruction": "string, optional",
  "artifact_retry_instruction": "string, optional"
}
"""

_ALLOWED_DECISIONS = {
    "expand_content",
    "revise_content",
    "generate_artifacts",
    "retry_artifacts",
    "publish",
    "fail",
}


class BlogPipelineError(RuntimeError):
    """Raised when the pipeline supervisor returns an invalid decision."""


class BlogPipelineAgent:
    """Supervisor agent that routes the LangGraph workflow."""

    def __init__(self, llm_client: BlogLlmClient | None = None):
        self.llm_client = llm_client or BlogLlmClient(config=PIPELINE_LLM_CONFIG)

    async def think(self, state: BlogGraphState) -> BlogPipelineDecision:
        return await self._decide(build_think_observation(state))

    async def review_content(self, state: BlogGraphState) -> BlogPipelineDecision:
        return await self._decide(build_content_review_observation(state))

    async def review_artifacts(self, state: BlogGraphState) -> BlogPipelineDecision:
        return await self._decide(build_artifact_review_observation(state))

    async def _decide(self, observation: str) -> BlogPipelineDecision:
        messages = [
            {"role": "system", "content": PIPELINE_SYSTEM_PROMPT},
            {"role": "user", "content": observation},
        ]
        raw_response = await self.llm_client.chat_completion(messages)
        return parse_pipeline_decision(raw_response)


def build_think_observation(state: BlogGraphState) -> str:
    idea = state.idea
    return _json_observation(
        "MainAgentThink",
        {
            "idea_key": idea.key if idea else "",
            "idea_frontmatter": idea.frontmatter if idea else {},
            "has_expanded_post": state.expanded_post is not None,
            "main_round": state.main_round,
            "errors": state.errors,
            "allowed_decisions": ["expand_content", "revise_content", "fail"],
        },
    )


def build_content_review_observation(state: BlogGraphState) -> str:
    post = state.expanded_post
    return _json_observation(
        "MainAgentReviewContent",
        {
            "expanded_post": post.__dict__ if post else None,
            "subagent_plan": [item.__dict__ for item in state.subagent_plan],
            "main_round": state.main_round,
            "errors": state.errors,
            "allowed_decisions": ["revise_content", "generate_artifacts", "fail"],
        },
    )


def build_artifact_review_observation(state: BlogGraphState) -> str:
    return _json_observation(
        "MainAgentReviewArtifacts",
        {
            "expanded_post_slug": state.expanded_post.slug if state.expanded_post else "",
            "html_artifact": state.html_artifact.__dict__ if state.html_artifact else None,
            "image_artifact": state.image_artifact.__dict__ if state.image_artifact else None,
            "artifact_round": state.artifact_round,
            "html_retry_count": state.html_retry_count,
            "image_retry_count": state.image_retry_count,
            "errors": state.errors,
            "allowed_decisions": ["retry_artifacts", "publish", "fail"],
        },
    )


def parse_pipeline_decision(raw: str) -> BlogPipelineDecision:
    payload = _parse_json_object(raw)
    decision = str(payload.get("decision") or "").strip()
    if decision not in _ALLOWED_DECISIONS:
        raise BlogPipelineError(f"Invalid pipeline decision: {decision}")

    reason = str(payload.get("reason") or "").strip()
    if not reason:
        raise BlogPipelineError("Pipeline decision requires a reason.")

    return BlogPipelineDecision(
        decision=decision,
        reason=reason,
        content_revision_instruction=str(
            payload.get("content_revision_instruction") or ""
        ).strip(),
        artifact_retry_instruction=str(payload.get("artifact_retry_instruction") or "").strip(),
        raw_response=raw,
    )


def _json_observation(node_name: str, payload: dict[str, Any]) -> str:
    return (
        f"## Node\n{node_name}\n\n"
        "## Observation JSON\n"
        f"{json.dumps(payload, indent=2, ensure_ascii=False)}"
    )


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise BlogPipelineError("Pipeline response did not contain a JSON object.")
        parsed = json.loads(match.group())

    if not isinstance(parsed, dict):
        raise BlogPipelineError("Pipeline response must be a JSON object.")
    return parsed
