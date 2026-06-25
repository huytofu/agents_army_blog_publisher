"""ReAct-style supervisor for the blog generation graph."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from blog_manager.config import PIPELINE_LLM_CONFIG
from blog_manager.schemas import AgentInvocation, BlogGraphState, BlogPipelineDecision
from blog_manager.services.llm_client import BlogLlmClient

logger = logging.getLogger(__name__)

PIPELINE_SYSTEM_PROMPT = """You are BlogPipelineAgent, the ReAct-style supervisor for the Entourage blog publisher graph.

ROLE:
- Observe the current graph state.
- Reason privately about the next safest workflow action.
- Return exactly one strict JSON decision.

ORCHESTRATION RESPONSIBILITIES:
- You are the only agent-level orchestrator. BlogExpansionAgent writes/expands/revises content; HTML/Image subagents create local artifacts.
- At MainAgentThink, inspect the idea and decide whether to start content expansion or fail.
- At MainAgentReviewContent, inspect the expanded post, safety notes, excerpt, and image brief. Decide whether to revise content, generate artifacts, or fail.
- At FinalizeSubagentsPlan, inspect the expanded post and default subagent plan. Return finalized HTML/Image subagent instructions for subsequent artifact generation, or fail.
- At MainAgentReviewArtifacts, inspect local artifact descriptors and validation errors. Decide whether to retry artifact generation, publish, or fail.
- Use `content_revision_instruction` only when choosing `revise_content`; make it specific enough for BlogExpansionAgent to revise content.
- Use `artifact_retry_instruction` only when choosing `retry_artifacts`; make it specific enough for the graph/subagents to address the artifact issue.
- Use `subagent_plan` only at FinalizeSubagentsPlan; include complete instructions for both `html_subagent` and `image_subagent`.
- Use `fail` with a concise reason when the workflow cannot safely continue (publisher valiation errors/graph errors/artifact generation retries exhausted or repeatedly failed)

BOUNDARIES:
- Do not write/expand/revise blog content yourself. Instead, output decision with instructions towards BlogExpansionAgent.
- Do not render HTML, generate images, or invoke tools yourself. Instead, output decision with instructions towards HTML/Image subagents.
- Do not perform S3 operations. Publisher graph nodes own S3 writes.
- Do not return chain-of-thought. Return a concise reason only.

ALLOWED DECISIONS:
- expand_content: use when no expanded post exists yet.
- revise_content: use when post has NSFW issues/hate speech/extreme religious or political views/etc.
- generate_artifacts: use when content is good enough and local HTML/image artifacts should be produced.
- retry_artifacts: use when artifacts failed validation but retry budget remains.
- publish: use only when content and artifacts are valid.
- fail: use when the workflow cannot safely continue.

PUBLISHING RULES:
- Only choose publish after both HTML and image local artifacts exist, have the expected content types, and match the expected `blog/<slug>/...` keys.
- Never choose publish just because content is good; artifact generation and validation must have happened first.

OUTPUT:
Return ONLY valid JSON with this schema (subagent_plan is optional except for at FinalizeSubagentsPlan):
{
  "decision": "expand_content|revise_content|generate_artifacts|retry_artifacts|publish|fail",
  "reason": "short user-safe explanation",
  "content_revision_instruction": "string, optional",
  "artifact_retry_instruction": "string, optional",
  "subagent_plan": [
    {"name": "html_subagent", "purpose": "string", "instructions": "string"},
    {"name": "image_subagent", "purpose": "string", "instructions": "string"}
  ]
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

    async def finalize_subagents_plan(
        self,
        state: BlogGraphState,
        default_plan: list[AgentInvocation],
    ) -> BlogPipelineDecision:
        return await self._decide(build_finalize_subagents_observation(state, default_plan))

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
            "allowed_decisions": ["expand_content", "fail"],
        },
    )


def build_content_review_observation(state: BlogGraphState) -> str:
    post = state.expanded_post
    return _json_observation(
        "MainAgentReviewContent",
        {
            "expanded_post": _post_payload(post),
            "main_round": state.main_round,
            "errors": state.errors,
            "allowed_decisions": ["revise_content", "generate_artifacts", "fail"],
        },
    )


def build_finalize_subagents_observation(
    state: BlogGraphState,
    default_plan: list[AgentInvocation],
) -> str:
    post = state.expanded_post
    return _json_observation(
        "FinalizeSubagentsPlan",
        {
            "expanded_post": _post_payload(post),
            "default_subagent_plan": [item.__dict__ for item in default_plan],
            "main_round": state.main_round,
            "errors": state.errors,
            "allowed_decisions": ["generate_artifacts", "fail"],
        },
    )


def build_artifact_review_observation(state: BlogGraphState) -> str:
    return _json_observation(
        "MainAgentReviewArtifacts",
        {
            "expanded_post_slug": state.expanded_post.slug if state.expanded_post else "",
            "html_artifact": _artifact_payload(state.html_artifact),
            "image_artifact": _artifact_payload(state.image_artifact),
            "image_artifacts": [_artifact_payload(artifact) for artifact in state.image_artifacts],
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
        subagent_plan=_subagent_plan_from_payload(payload),
        raw_response=raw,
    )


def _subagent_plan_from_payload(payload: dict[str, Any]) -> list[AgentInvocation]:
    raw_plan = payload.get("subagent_plan")
    if not isinstance(raw_plan, list):
        return []

    plan: list[AgentInvocation] = []
    for item in raw_plan:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        purpose = str(item.get("purpose") or "").strip()
        instructions = str(item.get("instructions") or "").strip()
        if not name or not purpose or not instructions:
            continue
        plan.append(
            AgentInvocation(
                name=name,
                purpose=purpose,
                instructions=instructions,
            )
        )
    return plan


def _json_observation(node_name: str, payload: dict[str, Any]) -> str:
    return (
        f"## Node\n{node_name}\n\n"
        "## Observation JSON\n"
        f"{json.dumps(payload, indent=2, ensure_ascii=False)}"
    )


def _post_payload(post: Any | None) -> dict[str, Any] | None:
    if post is None:
        return None
    return {
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
        "seo_title": post.seo_title,
        "seo_description": post.seo_description,
        "primary_keyword": post.primary_keyword,
        "search_intent": post.search_intent,
        "category": post.category,
        "faq_items": post.faq_items,
        "citation_suggestions": post.citation_suggestions,
        "safety_notes": post.safety_notes,
    }


def _artifact_payload(artifact: Any | None) -> dict[str, Any] | None:
    if artifact is None:
        return None
    return {
        "local_path": artifact.local_path,
        "relative_key": artifact.relative_key,
        "content_type": artifact.content_type,
        "metadata": artifact.metadata,
    }


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
