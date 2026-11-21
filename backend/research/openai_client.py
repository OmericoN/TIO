import json
import logging
import time
from http.client import HTTPException
from typing import TypeVar
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel


logger = logging.getLogger(__name__)
OutputModel = TypeVar("OutputModel", bound=BaseModel)


class MatchProcessingError(RuntimeError):
    """The provider failed or returned an unusable editorial decision."""


class OpenAIClient:
    def __init__(self, api_key: str, model: str = "gpt-5-mini"):
        if not api_key.strip():
            raise RuntimeError("Set OPENAI_API_KEY in the environment or repository root .env.")
        self.api_key = api_key
        self.model = model

    RETRY_STATUS = {429, 500, 502, 503, 504}
    RETRY_DELAYS = (1, 3)  # seconds before the second and third attempts

    def _post(self, request, stage):
        """Sends the request, retrying OpenAI's transient failures (rate limits, 5xx, timeouts) twice."""
        for attempt, delay in enumerate((*self.RETRY_DELAYS, None)):
            try:
                with urlopen(request, timeout=90) as response:
                    return json.load(response)
            except HTTPError as exc:
                if delay is None or exc.code not in self.RETRY_STATUS:
                    raise
                logger.warning("OpenAI HTTP %d during %s; retrying (attempt %d)", exc.code, stage, attempt + 2)
            except (TimeoutError, URLError):
                if delay is None:
                    raise
                logger.warning("OpenAI connection failed during %s; retrying (attempt %d)", stage, attempt + 2)
            time.sleep(delay)

    def generate(
        self, stage: str, instructions: str, data: dict, output_type: type[OutputModel]
    ) -> OutputModel:
        payload = {
            "model": self.model,
            "store": False,
            "messages": [
                {"role": "developer", "content": instructions},
                {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": stage,
                    "strict": True,
                    "schema": output_type.model_json_schema(),
                },
            },
        }
        request = Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        request.add_unredirected_header("Authorization", f"Bearer {self.api_key}")
        try:
            result = self._post(request, stage)
            usage = result["usage"]
            logger.info(
                "persona_match stage=%s model=%s input_tokens=%d cached_input_tokens=%d output_tokens=%d",
                stage, self.model, usage["prompt_tokens"],
                (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0),
                usage["completion_tokens"],
            )
            choice = result["choices"][0]
            message = choice["message"]
            if choice["finish_reason"] != "stop" or message.get("refusal"):
                raise MatchProcessingError(f"OpenAI did not complete {stage}.")
            return output_type.model_validate_json(message["content"], strict=True)
        except HTTPError as exc:
            detail = f"OpenAI returned HTTP {exc.code} during {stage}."
            if exc.code == 401:
                detail += " Check OPENAI_API_KEY and restart the backend."
            raise MatchProcessingError(detail) from exc
        except (TimeoutError, URLError) as exc:
            raise MatchProcessingError(f"The OpenAI request failed to connect or timed out during {stage}.") from exc
        except (OSError, HTTPException, ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            raise MatchProcessingError(f"OpenAI could not process {stage}.") from exc
