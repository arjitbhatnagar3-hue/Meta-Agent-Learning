"""Hugging Face Inference Providers implementation."""

import asyncio
import logging

from huggingface_hub import AsyncInferenceClient
from huggingface_hub.errors import HFValidationError

from backend.config import Settings
from backend.llm.base import LLMResponse


logger = logging.getLogger(__name__)


class HuggingFaceProvider:
    """Call hosted chat models through Hugging Face."""

    def __init__(self, settings: Settings) -> None:
        token = settings.hf_token.get_secret_value()

        if not token:
            raise ValueError(
                "HF_TOKEN is required in the worker environment"
            )

        self.settings = settings

        self.client = AsyncInferenceClient(
            provider="auto",
            token=token,
            timeout=settings.hf_timeout_seconds,
        )

        # All agents share this provider object.
        #
        # The semaphore stops several specialist agents from
        # sending large-model requests at exactly the same time.
        self._semaphore = asyncio.Semaphore(
            settings.hf_max_concurrent_requests
        )

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        """Make a limited and retried chat request."""

        last_error: Exception | None = None

        for attempt in range(
            1,
            self.settings.hf_max_retries + 1,
        ):
            try:
                # Only the configured number of requests can
                # enter this section simultaneously.
                async with self._semaphore:
                    output = (
                        await self.client.chat.completions.create(
                            model=model,
                            messages=messages,
                            max_tokens=(
                                max_tokens
                                or self.settings.hf_max_tokens
                            ),
                            temperature=(
                                self.settings.hf_temperature
                                if temperature is None
                                else temperature
                            ),
                            stream=False,
                        )
                    )

                content = output.choices[0].message.content

                if not content:
                    raise RuntimeError(
                        "Hugging Face returned empty content"
                    )

                usage = getattr(
                    output,
                    "usage",
                    None,
                )

                return LLMResponse(
                    content=str(content),
                    model=model,
                    prompt_tokens=getattr(
                        usage,
                        "prompt_tokens",
                        None,
                    ),
                    completion_tokens=getattr(
                        usage,
                        "completion_tokens",
                        None,
                    ),
                )

            except HFValidationError as exc:
                raise ValueError(
                    "Invalid Hugging Face model repository ID. "
                    "When using AsyncInferenceClient, use "
                    "'organization/model' without a routing "
                    "suffix such as ':fastest'."
                ) from exc

            except ImportError as exc:
                raise RuntimeError(
                    "AsyncInferenceClient requires aiohttp. "
                    "Install dependencies with: "
                    "python -m pip install -r requirements.txt"
                ) from exc

            except Exception as exc:
                last_error = exc

                # Prevent the token from appearing in logs.
                safe_message = str(exc).replace(
                    self.settings.hf_token.get_secret_value(),
                    "[REDACTED]",
                )[:800]

                logger.warning(
                    "HF inference attempt %s/%s failed: %s: %s",
                    attempt,
                    self.settings.hf_max_retries,
                    type(exc).__name__,
                    safe_message,
                )

                if attempt < self.settings.hf_max_retries:
                    await asyncio.sleep(
                        min(
                            2 ** (attempt - 1),
                            8,
                        )
                    )

        error_type = (
            type(last_error).__name__
            if last_error
            else "UnknownError"
        )

        safe_final = (
            str(last_error).replace(
                self.settings.hf_token.get_secret_value(),
                "[REDACTED]",
            )[:800]
            if last_error
            else "No error details"
        )

        raise RuntimeError(
            "Hugging Face inference failed after retries: "
            f"{error_type}: {safe_final}"
        ) from last_error