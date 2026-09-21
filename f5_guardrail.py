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
        route_models: Optional[Union[List[str], str]] = None,
        **kwargs,
    ):
        # LiteLLM passes values from config.yaml; env vars keep local runs simple.
        self.api_key = self._resolve_config_value(api_key, "F5_GUARDRAILS_API_TOKEN")
        self.api_base = api_base or os.getenv(
            "F5_GUARDRAILS_API_BASE", "https://www.us1.calypsoai.app/backend/v1"
        )
        # flagOnly=false lets ScanAPI return blocking decisions as "blocked".
        self.flag_only = self._resolve_flag_only(flag_only)
        self.route_models = self._resolve_route_models(route_models)
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

    @staticmethod
    def _resolve_route_models(
        route_models: Optional[Union[List[str], str]],
    ) -> Optional[set]:
        if route_models is None:
            return None
        if isinstance(route_models, str):
            return {
                route_model.strip()
                for route_model in route_models.split(",")
                if route_model.strip()
            }
        return set(route_models)

    async def apply_guardrail(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """
        Scans the prompt with F5 AI Security before LiteLLM calls the model.
        Tailored to LiteLLM v1.85, which passes a dict-like inputs payload.
        """
        print("F5 Guardrail: async_pre_call_hook running...", flush=True)

        def _get_field(value: Any, field: str) -> Any:
            if isinstance(value, dict):
                return value.get(field)
            return getattr(value, field, None)

        # ---------------------------------------------------------
        # Scenario A: LiteLLM v1.85 Schema (message list of dicts)
        # ---------------------------------------------------------
        raw_inputs = kwargs.get("inputs")
        request_data = kwargs.get("request_data")
        data = kwargs.get("data")
        proxy_server_request = kwargs.get("proxy_server_request")

        request_model = (
            _get_field(request_data, "model")
            or _get_field(data, "model")
            or _get_field(raw_inputs, "model")
        )
        if not request_model and proxy_server_request is not None:
            body = _get_field(proxy_server_request, "body")
            if isinstance(body, dict):
                request_model = body.get("model")

        if self.route_models and request_model not in self.route_models:
            print(
                f"F5 Guardrail: skipping model '{request_model}' for routes "
                f"{sorted(self.route_models)}",
                flush=True,
            )
            return raw_inputs

        if isinstance(raw_inputs, dict):
            texts = raw_inputs.get("texts")
            if isinstance(texts, list):
                redacted_texts = []
                for text_to_scan in texts:
                    if isinstance(text_to_scan, str) and text_to_scan:
                        print(
                            f"F5 Guardrail (v1.85): Scanning text: '{text_to_scan}'",
                            flush=True,
                        )
                        response_data = await self._check_with_api(text_to_scan)
                        redacted_texts.append(
                            self._apply_scan_result(
                                text=text_to_scan,
                                response_data=response_data,
                            )
                        )
                    else:
                        redacted_texts.append(text_to_scan)

                raw_inputs["texts"] = redacted_texts
                print(
                    "F5 Guardrail (v1.85): Scan complete. Returning modified inputs.",
                    flush=True,
                )
                return raw_inputs

            structured_messages = raw_inputs.get("structured_messages")
            if isinstance(structured_messages, list) and structured_messages:
                last_message = next(
                    (
                        message
                        for message in reversed(structured_messages)
                        if isinstance(message, dict) and message.get("role") == "user"
                    ),
                    structured_messages[-1],
                )
                text_to_scan = (
                    last_message.get("content", "")
                    if isinstance(last_message, dict)
                    else ""
                )

                if isinstance(text_to_scan, str) and text_to_scan:
                    print(
                        f"F5 Guardrail (v1.85): Scanning structured message: '{text_to_scan}'",
                        flush=True,
                    )
                    response_data = await self._check_with_api(text_to_scan)
                    last_message["content"] = self._apply_scan_result(
                        text=text_to_scan,
                        response_data=response_data,
                    )
                    print(
                        "F5 Guardrail (v1.85): Scan complete. Returning modified inputs.",
                        flush=True,
                    )

                return raw_inputs

        inputs = raw_inputs if isinstance(raw_inputs, list) else None
        if inputs is None and isinstance(raw_inputs, dict):
            inputs = raw_inputs.get("inputs") or raw_inputs.get("messages")
        if inputs is None:
            inputs = kwargs.get("messages")
        if inputs is None and request_data is not None:
            inputs = _get_field(request_data, "inputs") or _get_field(
                request_data, "messages"
            )
        if inputs is None and data is not None:
            inputs = _get_field(data, "inputs") or _get_field(data, "messages")
        if inputs is None and proxy_server_request is not None:
            body = _get_field(proxy_server_request, "body")
            if isinstance(body, dict):
                inputs = body.get("inputs") or body.get("messages")
        if inputs is None and args and isinstance(args[0], list):
            inputs = args[0]

        if inputs is not None and isinstance(inputs, list):
            if not inputs:
                print("F5 Guardrail: Empty message list skipped.", flush=True)
                return inputs

            last_message = next(
                (
                    message
                    for message in reversed(inputs)
                    if isinstance(message, dict) and message.get("role") == "user"
                ),
                inputs[-1],
            )
            text_to_scan = (
                last_message.get("content", "") if isinstance(last_message, dict) else ""
            )

            if isinstance(text_to_scan, str) and text_to_scan:
                print(f"F5 Guardrail (v1.85): Scanning text: '{text_to_scan}'", flush=True)
                response_data = await self._check_with_api(text_to_scan)
                redacted_text = self._apply_scan_result(
                    text=text_to_scan,
                    response_data=response_data,
                )
                last_message["content"] = redacted_text
                print(
                    "F5 Guardrail (v1.85): Scan complete. Returning modified inputs.",
                    flush=True,
                )
            else:
                print("F5 Guardrail (v1.85): Empty prompt text skipped.", flush=True)

            return inputs

        # ---------------------------------------------------------
        # Fallback: No recognizable input format matched
        # ---------------------------------------------------------
        print(
            "F5 Guardrail: No valid LiteLLM v1.85 'inputs' payload found. "
            "Skipping scan.",
            flush=True,
        )
        kwarg_types = {key: type(value).__name__ for key, value in kwargs.items()}
        print(
            f"F5 Guardrail: arg_types={[type(arg).__name__ for arg in args]}, "
            f"kwarg_types={kwarg_types}, "
            f"kwarg_keys={list(kwargs.keys())}",
            flush=True,
        )
        return raw_inputs

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
