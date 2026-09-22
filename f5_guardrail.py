# Copyright F5, Inc. 2026
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
from typing import Any, List, Optional, Union

from litellm.integrations.custom_guardrail import CustomGuardrail
from litellm.llms.custom_httpx.http_handler import (
    get_async_httpx_client,
    httpxSpecialProvider,
)
from litellm.types.guardrails import PiiEntityType


class f5Guardrail(CustomGuardrail):
    def __init__(
        self,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        flag_only: Optional[Union[bool, str]] = None,
        **kwargs,
    ):
        # LiteLLM passes values from config.yaml; env vars keep local runs simple.
        self.api_key = self._resolve_config_value(api_key, "F5_GUARDRAILS_API_TOKEN")
        self.api_base = self._resolve_config_value(
            api_base,
            "F5_GUARDRAILS_API_BASE",
        ) or "https://www.us1.calypsoai.app/backend/v1"
        # flagOnly=false lets ScanAPI return blocking decisions as "blocked".
        self.flag_only = self._resolve_flag_only(flag_only)
        super().__init__(**kwargs)

    @staticmethod
    def _resolve_config_value(value: Optional[str], fallback_env_key: str) -> Optional[str]:
        if value and value.startswith("os.environ/"):
            value = os.getenv(value.split("os.environ/", 1)[1])
        return value or os.getenv(fallback_env_key)

    @staticmethod
    def _resolve_flag_only(flag_only: Optional[Union[bool, str]]) -> bool:
        # Accept both YAML booleans and string values from config.
        if flag_only is None:
            return False
        if isinstance(flag_only, str):
            return flag_only.lower() in ("1", "true", "yes", "on")
        return flag_only

    async def apply_guardrail(
        self,
        *args: Any,
        text: Optional[str] = None,
        language: Optional[str] = None,  # unused
        entities: Optional[List[PiiEntityType]] = None,  # unused
        request_data: Optional[dict] = None,  # unused
        **kwargs: Any,
    ) -> Any:
        """
        Scan the prompt with F5 AI Security before LiteLLM calls the model.

        Return the original or redacted text to allow the call. Raise an
        exception to block the call.
        """
        inputs = kwargs.get("inputs")
        if inputs is None and args and isinstance(args[0], dict):
            inputs = args[0]

        if isinstance(inputs, dict):
            texts = inputs.get("texts")
            if isinstance(texts, list):
                inputs["texts"] = [
                    await self._scan_text(text_to_scan)
                    if isinstance(text_to_scan, str) and text_to_scan
                    else text_to_scan
                    for text_to_scan in texts
                ]
                return inputs
            return inputs

        if text is None and args and isinstance(args[0], str):
            text = args[0]

        if not text:
            return text

        return await self._scan_text(text)

    async def _scan_text(self, text: str) -> str:
        response_data = await self._check_with_api(text)
        return self._apply_scan_result(text=text, response_data=response_data)

    def _apply_scan_result(self, text: str, response_data: dict) -> str:
        # ScanAPI returns result.outcome as the normalized policy decision.
        result = response_data.get("result")
        if not isinstance(result, dict):
            raise Exception("F5 Guardrails scan response missing valid result object")

        outcome = result.get("outcome")
        if outcome == "cleared":
            return text
        if outcome == "redacted":
            redacted = response_data.get("redactedInput")
            if not redacted:
                raise Exception("Redacted outcome without redactedInput")
            return redacted

        # flagged indicates a guardrail match that is not configured to block.
        # Allow the workflow to continue with the original input.
        if outcome == "flagged":
            return text

        # blocked is returned when ScanAPI evaluates a blocking guardrail with
        # flagOnly=false. Stop the request before it reaches the model.
        if outcome == "blocked":
            raise Exception(self._policy_violation_message(response_data, outcome))

        # Unknown or missing outcomes fail closed so ScanAPI changes do not
        # silently allow untrusted input through to the model.
        raise Exception(f"Unexpected or missing F5 Guardrails outcome: {outcome}")

    @staticmethod
    def _policy_violation_message(response_data: dict, outcome: Any) -> str:
        result = response_data.get("result")
        if isinstance(result, dict):
            reason = result.get("reason") or result.get("message")
            if reason:
                return str(reason)
        return response_data.get("reason") or f"Policy violation: {outcome}"

    async def _check_with_api(self, text: str) -> dict:
        async_client = get_async_httpx_client(
            llm_provider=httpxSpecialProvider.LoggingCallback
        )
        if not self.api_key:
            raise Exception("F5_GUARDRAILS_API_TOKEN is not configured")

        response = await async_client.post(
            f"{self.api_base}/scans",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            json={"input": text, "flagOnly": self.flag_only},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()
