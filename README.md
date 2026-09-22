# F5 AI Security LiteLLM Integration

This repository shows how to call F5 AI Security ScanAPI as a LiteLLM pre-call custom guardrail.

The guardrail scans user input before LiteLLM forwards the request to the model. It allows cleared prompts, forwards redacted prompts when ScanAPI returns `redactedInput`, allows flagged prompts to continue, and blocks prompts when ScanAPI returns `blocked`.

This is a starter integration intended to get traffic flowing to and from F5 AI Security guardrails through LiteLLM. It does not cover every ScanAPI option, every policy configuration, or every possible AI workflow integration pattern.

This example is not a fully maintained product integration. Changes in LiteLLM or F5 AI Security APIs may require code or configuration updates.

## Files

- `f5_guardrail.py`: LiteLLM custom guardrail implementation.
- `config.yaml`: Minimal LiteLLM proxy configuration.
- `config.multi-project.example.yaml`: Example routing multiple application aliases to different F5 projects.
- `.env.example`: Required environment variables.
- `requirements.txt`: Python dependencies for running the example.

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

Install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Set environment variables:

```bash
export F5_GUARDRAILS_API_TOKEN="your-f5-token"
export OPENAI_API_KEY="your-openai-token"
```

Start the LiteLLM proxy:

```bash
litellm --config config.yaml
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

## Configuration

The guardrail is registered in `config.yaml`:

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
      api_key: os.environ/F5_GUARDRAILS_API_TOKEN
      api_base: https://www.us1.calypsoai.app/backend/v1
      flag_only: false
```

The request `model` must match a `model_name` in `model_list`; the included config exposes `gpt-4o-mini`.

`guardrails` under `model_list[].litellm_params` enables the guardrail for that model.

`mode: pre_call` tells LiteLLM to scan input before calling the model.

`default_on: true` applies the guardrail automatically.

`flag_only: false` sends `flagOnly: false` to ScanAPI, allowing F5 AI Security to return `blocked` for blocking guardrails. If ScanAPI returns `flagged`, this example allows the LiteLLM request to continue.

`api_base` defaults to the US region shown above. You can also set `F5_GUARDRAILS_API_BASE` in the environment if you need a different F5 AI Security base URL.

## Multiple Applications and Policies

LiteLLM can expose different application-facing model aliases while sending traffic to the same underlying provider model. This is useful when each application needs a different F5 AI Security project or policy.

The recommended mapping is:

```text
application -> LiteLLM model_name alias -> LiteLLM guardrail -> F5 API token -> F5 project/policy
```

For example, `config.multi-project.example.yaml` defines:

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
      default_on: false
      api_key: os.environ/F5_HR_PROJECT_API_TOKEN
      api_base: os.environ/F5_GUARDRAILS_API_BASE
      flag_only: false

  - guardrail_name: f5-makeup-project-input
    litellm_params:
      guardrail: f5_guardrail.f5Guardrail
      mode: pre_call
      default_on: false
      api_key: os.environ/F5_MAKEUP_PROJECT_API_TOKEN
      api_base: os.environ/F5_GUARDRAILS_API_BASE
      flag_only: false
```

Both aliases use `openai/gpt-4o-mini`, but LiteLLM applies different guardrails and therefore different F5 project tokens. In production, pair this with LiteLLM authentication and per-key model allowlists so each application can only call its assigned alias.

To run the multi-project example with Docker:

```bash
docker run -d \
  --name litellm-test \
  -p 4000:4000 \
  -v "$(pwd)":/app/src \
  -e PYTHONPATH="/app/src" \
  -e OPENAI_API_KEY="${OPENAI_API_KEY}" \
  -e F5_GUARDRAILS_API_BASE="${F5_GUARDRAILS_API_BASE}" \
  -e F5_HR_PROJECT_API_TOKEN="${F5_HR_PROJECT_API_TOKEN}" \
  -e F5_MAKEUP_PROJECT_API_TOKEN="${F5_MAKEUP_PROJECT_API_TOKEN}" \
  ghcr.io/berriai/litellm:latest \
  --config /app/src/config.multi-project.example.yaml --port 4000 --detailed_debug
```

## Notes

This example scans prompts before the model call. To moderate the full AI workflow, add scans around tool use and generated responses as appropriate for your application.

Treat this repository as a reference starting point. Production deployments should tune F5 AI Security policies, request metadata, error handling, logging, and scan placement for the application architecture they are protecting.
