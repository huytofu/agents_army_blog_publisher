"""LangGraph wiring for the blog generation and publishing workflow."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import logging
from typing import Any, Callable

from blog_manager.agents import (
    BlogExpansionAgent,
    BlogPipelineAgent,
    HtmlAgent,
    ImageAgent,
)
from blog_manager.config import WORKER_CONFIG
from blog_manager.constants import (
    COVER_IMAGE_CONTENT_TYPE,
    COVER_IMAGE_FILENAME,
    POST_HTML_CONTENT_TYPE,
    POST_HTML_FILENAME,
)
from blog_manager.schemas import (
    AgentInvocation,
    BlogGraphState,
    BlogIdea,
    BlogPipelineDecision,
    HtmlArtifactState,
    ImageArtifactState,
)
from blog_manager.services.local_artifact_service import LocalArtifactService
from blog_manager.services.s3_blog_store import S3BlogStore

try:
    from langgraph.graph import END, StateGraph  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - optional deployment dependency
    END = "__end__"
    StateGraph = None

logger = logging.getLogger(__name__)


class BlogGraphError(RuntimeError):
    """Raised when the blog generation graph cannot continue safely."""


class BlogGenerationWorkflow:
    """Dependency container for graph nodes.

    Subagent branches receive only local agents/tools. S3 store access is used
    only by publisher nodes on this class.
    """

    def __init__(
        self,
        *,
        pipeline_agent: BlogPipelineAgent | None = None,
        expansion_agent: BlogExpansionAgent | None = None,
        html_agent: HtmlAgent | None = None,
        image_agent: ImageAgent | None = None,
        artifact_service: LocalArtifactService | None = None,
        s3_store: S3BlogStore | None = None,
        config: dict[str, Any] | None = None,
    ):
        self.pipeline_agent = pipeline_agent or BlogPipelineAgent()
        self.expansion_agent = expansion_agent or BlogExpansionAgent()
        self.html_agent = html_agent or HtmlAgent()
        self.image_agent = image_agent or ImageAgent()
        self.artifact_service = artifact_service or LocalArtifactService()
        self.s3_store = s3_store
        self.config = config or WORKER_CONFIG

    async def load_idea(self, state: BlogGraphState) -> BlogGraphState:
        if state.idea is None:
            return _append_error(state, "Graph state is missing an idea.")
        return state

    async def main_think(self, state: BlogGraphState) -> BlogGraphState:
        if _rounds_exhausted(state, self.config):
            return _with_decision(state, _fail_decision("Main agent round limit reached."))
        decision = await self.pipeline_agent.think(state)
        return _with_decision(replace(state, main_round=state.main_round + 1), decision)

    async def expand_content(self, state: BlogGraphState) -> BlogGraphState:
        if state.idea is None:
            return _append_error(state, "Cannot expand content without an idea.")
        revision_instruction = ""
        if state.main_decision:
            revision_instruction = state.main_decision.content_revision_instruction
        result = await self.expansion_agent.expand_idea(
            state.idea,
            revision_instruction=revision_instruction,
        )
        return replace(
            state,
            expanded_post=result.post,
            feed_entry=result.post.to_feed_entry(),
            subagent_plan=result.subagent_plan,
        )

    async def main_review_content(self, state: BlogGraphState) -> BlogGraphState:
        decision = await self.pipeline_agent.review_content(state)
        return _with_decision(replace(state, main_round=state.main_round + 1), decision)

    async def run_artifact_branches(self, state: BlogGraphState) -> BlogGraphState:
        if state.expanded_post is None:
            return _append_error(state, "Cannot generate artifacts without expanded content.")

        html_instructions = _instructions_for(state.subagent_plan, "html_subagent")
        image_instructions = _instructions_for(state.subagent_plan, "image_subagent")

        html_state = HtmlArtifactState(
            expanded_post=state.expanded_post,
            instructions=html_instructions,
            retry_count=state.html_retry_count,
        )
        image_state = ImageArtifactState(
            expanded_post=state.expanded_post,
            instructions=image_instructions,
            retry_count=state.image_retry_count,
        )
        html_result, image_result = await asyncio.gather(
            self.run_html_subgraph(html_state),
            self.run_image_subgraph(image_state),
        )

        return replace(
            state,
            html_artifact=html_result.artifact,
            image_artifact=image_result.artifact,
            html_retry_count=html_result.retry_count,
            image_retry_count=image_result.retry_count,
            artifact_round=state.artifact_round + 1,
            errors=[*state.errors, *html_result.errors, *image_result.errors],
        )

    async def run_html_subgraph(self, state: HtmlArtifactState) -> HtmlArtifactState:
        max_retries = int(self.config["SUBAGENT_MAX_RETRIES"])
        current = state
        while current.retry_count <= max_retries:
            try:
                artifact = await self.html_agent.create_html_artifact(
                    current.expanded_post,
                    instructions=current.instructions,
                    prior_errors=current.errors,
                )
                errors = self.artifact_service.validate_artifact(
                    artifact,
                    slug=current.expanded_post.slug,
                    filename=POST_HTML_FILENAME,
                    content_type=POST_HTML_CONTENT_TYPE,
                )
                if not errors:
                    return replace(current, artifact=artifact, errors=[])
                current = replace(
                    current,
                    artifact=artifact,
                    retry_count=current.retry_count + 1,
                    errors=errors,
                )
            except Exception as exc:
                current = replace(
                    current,
                    retry_count=current.retry_count + 1,
                    errors=[f"HTML artifact generation failed: {exc}"],
                )
        return current

    async def run_image_subgraph(self, state: ImageArtifactState) -> ImageArtifactState:
        max_retries = int(self.config["SUBAGENT_MAX_RETRIES"])
        current = state
        while current.retry_count <= max_retries:
            try:
                artifact = await self.image_agent.create_image_artifact(
                    current.expanded_post,
                    instructions=current.instructions,
                    prior_errors=current.errors,
                )
                errors = self.artifact_service.validate_artifact(
                    artifact,
                    slug=current.expanded_post.slug,
                    filename=COVER_IMAGE_FILENAME,
                    content_type=COVER_IMAGE_CONTENT_TYPE,
                )
                if not errors:
                    return replace(current, artifact=artifact, errors=[])
                current = replace(
                    current,
                    artifact=artifact,
                    retry_count=current.retry_count + 1,
                    errors=errors,
                )
            except Exception as exc:
                current = replace(
                    current,
                    retry_count=current.retry_count + 1,
                    errors=[f"Image artifact generation failed: {exc}"],
                )
        return current

    async def main_review_artifacts(self, state: BlogGraphState) -> BlogGraphState:
        decision = await self.pipeline_agent.review_artifacts(state)
        return _with_decision(replace(state, main_round=state.main_round + 1), decision)

    async def validate_publish_inputs(self, state: BlogGraphState) -> BlogGraphState:
        if state.expanded_post is None or state.feed_entry is None:
            return _append_error(state, "Publish validation failed: missing post metadata.")
        html_errors = self.artifact_service.validate_artifact(
            state.html_artifact,
            slug=state.expanded_post.slug,
            filename=POST_HTML_FILENAME,
            content_type=POST_HTML_CONTENT_TYPE,
        )
        image_errors = self.artifact_service.validate_artifact(
            state.image_artifact,
            slug=state.expanded_post.slug,
            filename=COVER_IMAGE_FILENAME,
            content_type=COVER_IMAGE_CONTENT_TYPE,
        )
        errors = [*html_errors, *image_errors]
        if errors:
            return replace(state, publish_ready=False, errors=[*state.errors, *errors])
        return replace(state, publish_ready=True)

    async def update_feed(self, state: BlogGraphState) -> BlogGraphState:
        if self.config.get("DRY_RUN"):
            logger.info("Dry run enabled; skipping posts feed update.")
            return state
        self._require_s3_store()
        if not state.publish_ready or state.feed_entry is None:
            return _append_error(state, "Feed update skipped: publish inputs are not ready.")
        self.s3_store.append_feed_entry(state.feed_entry)
        return state

    async def upload_assets(self, state: BlogGraphState) -> BlogGraphState:
        if self.config.get("DRY_RUN"):
            logger.info("Dry run enabled; skipping local artifact uploads.")
            return state
        self._require_s3_store()
        if state.html_artifact is None or state.image_artifact is None:
            return _append_error(state, "Asset upload skipped: artifacts are missing.")
        self.s3_store.upload_local_artifact(state.html_artifact)
        self.s3_store.upload_local_artifact(state.image_artifact)
        return state

    async def mark_processed(self, state: BlogGraphState) -> BlogGraphState:
        if self.config.get("DRY_RUN"):
            logger.info("Dry run enabled; skipping source idea processed update.")
            return state
        self._require_s3_store()
        if state.idea is None or state.expanded_post is None:
            return _append_error(state, "Mark processed skipped: idea or post is missing.")
        self.s3_store.mark_idea_processed(
            state.idea,
            slug=state.expanded_post.slug,
            post_key=f"blog/{state.expanded_post.slug}/{POST_HTML_FILENAME}",
        )
        return state

    async def cleanup_local(self, state: BlogGraphState) -> BlogGraphState:
        if state.expanded_post is not None:
            self.artifact_service.clear_post_artifacts(state.expanded_post.slug)
        return state

    async def fail(self, state: BlogGraphState) -> BlogGraphState:
        if state.main_decision and state.main_decision.reason:
            return _append_error(state, state.main_decision.reason)
        return state

    def _require_s3_store(self) -> None:
        if self.s3_store is None:
            raise BlogGraphError("S3 store is required for publisher graph nodes.")


def build_blog_generation_graph(workflow: BlogGenerationWorkflow | None = None) -> Any:
    """Build and compile the optional LangGraph workflow."""
    if StateGraph is None:
        raise BlogGraphError("langgraph is required to build the blog generation graph.")

    wf = workflow or BlogGenerationWorkflow()
    graph = StateGraph(BlogGraphState)
    graph.add_node("load_idea", wf.load_idea)
    graph.add_node("main_think", wf.main_think)
    graph.add_node("expand_content", wf.expand_content)
    graph.add_node("main_review_content", wf.main_review_content)
    graph.add_node("run_artifact_branches", wf.run_artifact_branches)
    graph.add_node("main_review_artifacts", wf.main_review_artifacts)
    graph.add_node("validate_publish_inputs", wf.validate_publish_inputs)
    graph.add_node("update_feed", wf.update_feed)
    graph.add_node("upload_assets", wf.upload_assets)
    graph.add_node("mark_processed", wf.mark_processed)
    graph.add_node("cleanup_local", wf.cleanup_local)
    graph.add_node("fail", wf.fail)

    graph.set_entry_point("load_idea")
    graph.add_edge("load_idea", "main_think")
    graph.add_conditional_edges(
        "main_think",
        route_main_think,
        {
            "expand_content": "expand_content",
            "revise_content": "expand_content",
            "fail": "fail",
        },
    )
    graph.add_edge("expand_content", "main_review_content")
    graph.add_conditional_edges(
        "main_review_content",
        route_content_review,
        {
            "revise_content": "expand_content",
            "generate_artifacts": "run_artifact_branches",
            "fail": "fail",
        },
    )
    graph.add_edge("run_artifact_branches", "main_review_artifacts")
    graph.add_conditional_edges(
        "main_review_artifacts",
        route_artifact_review,
        {
            "retry_artifacts": "run_artifact_branches",
            "publish": "validate_publish_inputs",
            "fail": "fail",
        },
    )
    graph.add_conditional_edges(
        "validate_publish_inputs",
        route_publish_validation,
        {
            "publish": "update_feed",
            "fail": "fail",
        },
    )
    graph.add_edge("update_feed", "upload_assets")
    graph.add_edge("upload_assets", "mark_processed")
    graph.add_edge("mark_processed", "cleanup_local")
    graph.add_edge("cleanup_local", END)
    graph.add_edge("fail", END)
    return graph.compile()


def initial_state(idea: BlogIdea) -> BlogGraphState:
    return BlogGraphState(idea=idea)


def route_main_think(state: BlogGraphState) -> str:
    return _decision_or_fail(state, {"expand_content", "revise_content", "fail"})


def route_content_review(state: BlogGraphState) -> str:
    return _decision_or_fail(state, {"revise_content", "generate_artifacts", "fail"})


def route_artifact_review(state: BlogGraphState) -> str:
    return _decision_or_fail(state, {"retry_artifacts", "publish", "fail"})


def route_publish_validation(state: BlogGraphState) -> str:
    if state.publish_ready and not state.errors:
        return "publish"
    return "fail"


def _decision_or_fail(state: BlogGraphState, allowed: set[str]) -> str:
    if state.main_decision is None:
        return "fail"
    if state.main_decision.decision not in allowed:
        return "fail"
    if state.main_decision.decision != "publish" and _rounds_exhausted(state, WORKER_CONFIG):
        return "fail"
    return state.main_decision.decision


def _rounds_exhausted(state: BlogGraphState, config: dict[str, Any]) -> bool:
    return state.main_round >= int(config["MAIN_AGENT_MAX_ROUNDS"])


def _with_decision(
    state: BlogGraphState,
    decision: BlogPipelineDecision,
) -> BlogGraphState:
    return replace(state, main_decision=decision)


def _append_error(state: BlogGraphState, error: str) -> BlogGraphState:
    return replace(state, errors=[*state.errors, error])


def _fail_decision(reason: str) -> BlogPipelineDecision:
    return BlogPipelineDecision(decision="fail", reason=reason)


def _instructions_for(plan: list[AgentInvocation], name: str) -> str:
    for item in plan:
        if item.name == name:
            return item.instructions
    return ""
