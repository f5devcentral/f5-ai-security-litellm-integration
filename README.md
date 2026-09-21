# F5 AI Security LiteLLM Integration

This repository shows how to call F5 AI Security ScanAPI from a LiteLLM custom guardrail.

This branch is intended for customer testing with **LiteLLM v1.85.0**. LiteLLM changed custom guardrail calling conventions across releases, and this branch is intentionally version-specific.

The default configuration scans user input before LiteLLM forwards the request to the model. It allows cleared prompts, forwards redacted prompts when ScanAPI returns `redactedInput`, allows flagged prompts to continue, and blocks prompts when ScanAPI returns `blocked`.

This example is not a fully maintained product integration. Changes in LiteLLM or F5 AI Security APIs may require code or configuration updates.

## Files

- `f5_guardrail.py`: LiteLLM custom guardrail implementation.
- `config.yaml`: Minimal LiteLLM proxy configuration.
- `config.multi-project.example.yaml`: Example routing multiple application aliases to different F5 projects.
- `.env.example`: Required environment variables.
- `requirements.txt`: Python dependencies for running the example.

## Version Scope

This fork has been tested against the LiteLLM Docker image:

```text
ghcr.io/berriai/litellm:1.85.0
```

LiteLLM v1.85 passes custom guardrail payloads as a dict-like `inputs` object, for example `{"texts": [...]}`. The guardrail implementation in this branch handles that shape and returns the same shape back to LiteLLM.

Keep the main branch focused on the latest LiteLLM behavior. Use this branch only for LiteLLM v1.85 testing.

## Behavior

| ScanAPI outcome | LiteLLM behavior |
| --- | --- |
| `cleared` | Forward the original prompt. |
| `redacted` | Forward `redactedInput` to the model. |
| `flagged` | Forward the original prompt and allow the workflow to continue. |
| `blocked` | Block the request. |
| missing or unknown | Fail closed and block the request. |

The example sends `flagOnly: false` to ScanAPI so blocking guardrails can return the `blocked` outcome. In this implementation, `flagged` is treated as a signal and `blocked` is treated as an enforcement decision.

## Where to Scan

F5 AI Security ScanAPI can be invoked at multiple points in an AI workflow:

- User input before it reaches the model.
- Tool call inputs before an agent invokes an external tool.
- Tool call outputs before they are added back into model context.
- Generated model responses before they are returned to an end user or downstream system.

This repository implements the first pattern with a LiteLLM `pre_call` guardrail. The same ScanAPI decision handling can be reused in application code, agent middleware, or a LiteLLM post-call guardrail for other workflow stages.

## Requirements

- Python 3.10 or later
- F5 AI Security API token with ScanAPI access
- A LiteLLM-supported model provider key, such as `OPENAI_API_KEY`

## Quick Start

Set environment variables. Do not commit real tokens.

```bash
export OPENAI_API_KEY="your-openai-api-key"
export F5_GUARDRAILS_API_TOKEN="your-f5-ai-security-token"
export F5_GUARDRAILS_API_BASE="https://www.us1.calypsoai.app/backend/v1"
```

Start LiteLLM v1.85 with Docker:

```bash
docker rm -f litellm-test 2>/dev/null || true

docker run -d \
  --name litellm-test \
  -p 4000:4000 \
  -v "$(pwd)":/app/src \
  -e PYTHONPATH="/app/src" \
  -e OPENAI_API_KEY="${OPENAI_API_KEY}" \
  -e F5_GUARDRAILS_API_TOKEN="${F5_GUARDRAILS_API_TOKEN}" \
  -e F5_GUARDRAILS_API_BASE="${F5_GUARDRAILS_API_BASE}" \
  ghcr.io/berriai/litellm:1.85.0 \
  --config /app/src/config.yaml --port 4000 --detailed_debug
```

Watch logs:

```bash
docker logs -f litellm-test
```

Send a request through LiteLLM:

```bash
curl http://localhost:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer anything" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [
      {
        "role": "user",
        "content": "What is 2 + 2?"
      }
    ]
  }'
```

Expected log lines when the guardrail runs:

```text
F5 Guardrail: async_pre_call_hook running...
F5 Guardrail (v1.85): Scanning text: '...'
F5 Guardrail (v1.85): Scan complete. Returning modified inputs.
```

## Configuration

The included `config.yaml` registers the guardrail and enables it for the `gpt-4o-mini` model:

```yaml
model_list:
  - model_name: gpt-4o-mini
    litellm_params:
      model: openai/gpt-4o-mini
      api_key: os.environ/OPENAI_API_KEY
      guardrails:
        - f5-guardrail

guardrails:
  - guardrail_name: f5-guardrail
    litellm_params:
      guardrail: f5_guardrail.f5Guardrail
      mode: pre_call
      default_on: true

litellm_settings:
  set_verbose: true
```

Important details:

- `guardrails` under `model_list[].litellm_params` enables the guardrail for that model.
- Do not use `callbacks` under `litellm_params`; LiteLLM may forward unknown parameters to OpenAI.
- `mode: pre_call` scans input before calling the model.
- `default_on: true` keeps the guardrail enabled by default.
- F5 credentials are read from `F5_GUARDRAILS_API_TOKEN` and `F5_GUARDRAILS_API_BASE`.

The code sends `flagOnly: false` to ScanAPI by default, allowing F5 AI Security to return `blocked` for blocking guardrails. If ScanAPI returns `flagged`, this example allows the LiteLLM request to continue.

`F5_GUARDRAILS_API_BASE` defaults to the US region shown above if unset.

## Multiple Applications and Policies

LiteLLM often sits in front of many applications. The clean mapping is:

```text
calling application -> LiteLLM model_name alias -> LiteLLM guardrail -> F5 API token -> F5 project/policy
```

For example, an HR assistant and a makeup assistant can use the same underlying OpenAI model while using different F5 AI Security projects:

```yaml
model_list:
  - model_name: hr-assistant
    litellm_params:
      model: openai/gpt-4o-mini
      api_key: os.environ/OPENAI_API_KEY
      guardrails:
        - f5-hr-project-input

  - model_name: makeup-assistant
    litellm_params:
      model: openai/gpt-4o-mini
      api_key: os.environ/OPENAI_API_KEY
      guardrails:
        - f5-makeup-project-input

guardrails:
  - guardrail_name: f5-hr-project-input
    litellm_params:
      guardrail: f5_guardrail.f5Guardrail
      mode: pre_call
      default_on: true
      route_models:
        - hr-assistant
      api_key: os.environ/F5_HR_PROJECT_API_TOKEN
      api_base: os.environ/F5_GUARDRAILS_API_BASE
      flag_only: false

  - guardrail_name: f5-makeup-project-input
    litellm_params:
      guardrail: f5_guardrail.f5Guardrail
      mode: pre_call
      default_on: true
      route_models:
        - makeup-assistant
      api_key: os.environ/F5_MAKEUP_PROJECT_API_TOKEN
      api_base: os.environ/F5_GUARDRAILS_API_BASE
      flag_only: false
```

In this pattern, the client chooses the application route by sending `model: "hr-assistant"` or `model: "makeup-assistant"` to LiteLLM. LiteLLM still calls `openai/gpt-4o-mini` behind the scenes, but it applies the guardrail attached to that route.

In production, pair this with LiteLLM authentication and per-key model allowlists so the HR application key can only call `hr-assistant`, the makeup application key can only call `makeup-assistant`, and so on. Do not rely only on client-side discipline to choose the correct model alias.

`default_on: true` is intentional in the multi-project example for LiteLLM v1.85. In this version, the proxy pre-call hook sees an empty `requested_guardrails` list before model routing metadata is fully applied. Setting `default_on: true` ensures LiteLLM calls the guardrail, while `route_models` inside `f5_guardrail.py` prevents the wrong F5 project from scanning the wrong app route.

The complete example is in `config.multi-project.example.yaml`.

## Response Scanning

To scan model output as well as user input, define a second guardrail entry using the same class with `mode: post_call`, then enable both guardrails for the model:

```yaml
model_list:
  - model_name: gpt-4o-mini
    litellm_params:
      model: openai/gpt-4o-mini
      api_key: os.environ/OPENAI_API_KEY
      guardrails:
        - f5-guardrail-input
        - f5-guardrail-output

guardrails:
  - guardrail_name: f5-guardrail-input
    litellm_params:
      guardrail: f5_guardrail.f5Guardrail
      mode: pre_call
      default_on: true

  - guardrail_name: f5-guardrail-output
    litellm_params:
      guardrail: f5_guardrail.f5Guardrail
      mode: post_call
      default_on: true
```

For non-streaming chat completions, a `post_call` guardrail can validate or redact generated text before the response is returned. For streaming responses, LiteLLM post-call guardrails run after the full stream has already been delivered, so they are useful for audit/logging but not for real-time blocking. Use a streaming iterator hook for real-time streaming enforcement.

The current implementation scans any `texts` list LiteLLM passes to `apply_guardrail`, so the same ScanAPI decision logic is reused for request and response text.

## Customer Test Checklist

- Pin LiteLLM to `ghcr.io/berriai/litellm:1.85.0`.
- Mount this repository into the container and set `PYTHONPATH=/app/src`.
- Pass only environment variable names or placeholder tokens in shared docs.
- Confirm logs show `F5 Guardrail (v1.85): Scanning text`.
- Test `cleared`, `redacted`, `flagged`, and `blocked` F5 policy outcomes.
- Do not share verbose LiteLLM logs without redacting provider API keys.

## Notes

This example scans prompts before the model call. To moderate the full AI workflow, add scans around tool use and generated responses as appropriate for your application.

Treat this repository as a reference starting point. Production deployments should tune F5 AI Security policies, request metadata, error handling, logging, and scan placement for the application architecture they are protecting.
