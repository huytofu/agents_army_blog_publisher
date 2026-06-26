"""Together / HuggingFace chat completion client for blog agents."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Iterable, cast

from blog_manager.config import LLM_CONFIG, get_hf_token, get_together_token
DEBUG = LLM_CONFIG["DEBUG"]

if TYPE_CHECKING:
    from together.types.chat.completion_create_params import Message as TogetherMessage

try:
    from together import AsyncTogether  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - optional provider dependency
    AsyncTogether = None

try:
    from huggingface_hub import AsyncInferenceClient  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - optional provider dependency
    AsyncInferenceClient = None

logger = logging.getLogger(__name__)


class BlogLlmError(RuntimeError):
    """Raised when all configured LLM providers fail."""


class BlogLlmClient:
    """Async chat client using Together first, then HuggingFace fallback."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or LLM_CONFIG

    async def chat_completion(self, messages: list[dict[str, str]]) -> str:
        """Return assistant text from the first successful configured provider."""
        choices, response = await self._try_together(messages)
        if DEBUG:
            print(f"Together choices: {choices}")
            print(f"Together response: {response}")
        if response:
            return response

        choices, response = await self._try_huggingface(messages, self.config.get("HF_MODEL", ""))
        if DEBUG:
            print(f"HuggingFace choices: {choices}")
            print(f"HuggingFace response: {response}")
        if response:
            return response

        for fallback_model in self.config.get("HF_FALLBACK_MODEL_IDS", []):
            choices, response = await self._try_huggingface(messages, fallback_model)
            if DEBUG:
                print(f"HuggingFace fallback model: {fallback_model}")
                print(f"HuggingFace choices: {choices}")
                print(f"HuggingFace response: {response}")
            if response:
                return response

        raise BlogLlmError("All blog LLM providers failed or returned empty output.")

    async def _try_together(self, messages: list[dict[str, str]]) -> tuple[list[Any], str]:
        model = self.config.get("TOGETHER_MODEL", "")
        token = get_together_token()
        if not model or not token or AsyncTogether is None:
            return [], ""

        client = AsyncTogether(api_key=token)
        try:
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=model,
                    messages=cast("Iterable[TogetherMessage]", messages),
                    max_tokens=self.config["MAX_TOKENS"],
                    temperature=self.config["TEMPERATURE"],
                    top_p=self.config["TOP_P"],
                ),
                timeout=self.config["TIMEOUT_SEC"],
            )
            return _extract_message_content(response)
        except Exception as exc:
            logger.warning("Together blog completion failed: %s", _format_provider_exception(exc))
            return [], ""
        finally:
            await _safe_close(client)

    async def _try_huggingface(self, messages: list[dict[str, str]], model: str) -> tuple[list[Any], str]:
        token = get_hf_token()
        if not model or not token or AsyncInferenceClient is None:
            return [], ""

        client = AsyncInferenceClient(
            token=token,
            model=model,
            provider=self.config["HF_PROVIDER"],
        )
        try:
            response = await asyncio.wait_for(
                client.chat_completion(
                    messages=messages,
                    max_tokens=self.config["MAX_TOKENS"],
                    temperature=self.config["TEMPERATURE"],
                    top_p=self.config["TOP_P"],
                ),
                timeout=self.config["TIMEOUT_SEC"],
            )
            return _extract_message_content(response)
        except Exception as exc:
            logger.warning(
                "HuggingFace blog completion failed model=%s error=%s",
                model,
                _format_provider_exception(exc),
            )
            return [], ""
        finally:
            await _safe_close(client)


async def _safe_close(client: Any | None) -> None:
    if client is None:
        return
    close_fn = getattr(client, "close", None)
    if not callable(close_fn):
        return
    result = close_fn()
    if asyncio.iscoroutine(result):
        await result


def _extract_message_content(response: Any) -> tuple[list[Any], str]:
    choices = getattr(response, "choices", None)
    if not choices:
        return [], ""
    first_choice = choices[0]
    message = getattr(first_choice, "message", {"content": ""})
    content = getattr(message, "content", "")
    return choices, str(content or "").strip()


def _format_provider_exception(exc: BaseException) -> str:
    details = [f"type={type(exc).__name__}"]
    message = str(exc).strip()
    if message:
        details.append(f"message={message}")
    details.append(f"repr={exc!r}")

    status_code = getattr(exc, "status_code", None)
    if status_code is not None:
        details.append(f"status_code={status_code}")

    body = getattr(exc, "body", None)
    if body is not None:
        details.append(f"body={_truncate_detail(repr(body))}")

    return " ".join(details)


def _truncate_detail(value: str, limit: int = 1000) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}..."
