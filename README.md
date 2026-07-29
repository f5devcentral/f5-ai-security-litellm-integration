# F5 AI Security LiteLLM Integration

This repository shows how to call F5 AI Security ScanAPI as a LiteLLM pre-call custom guardrail.

The guardrail scans user input before LiteLLM forwards the request to the model. It allows cleared prompts, forwards redacted prompts when ScanAPI returns `redactedInput`, and blocks prompts when ScanAPI returns `flagged` or `blocked`.

## Files

- `f5_guardrail.py`: LiteLLM custom guardrail implementation.
- `config.yaml`: Minimal LiteLLM proxy configuration.
- `.env.example`: Required environment variables.
- `requirements.txt`: Python dependencies for running the example.

## Behavior

| ScanAPI outcome | LiteLLM behavior |
| --- | --- |
| `cleared` | Forward the original prompt. |
| `redacted` | Forward `redactedInput` to the model. |
| `flagged` | Block the request. |
| `blocked` | Block the request. |
| missing or unknown | Fail closed and block the request. |

The example sends `flagOnly: false` to ScanAPI so blocking guardrails can return the `blocked` outcome. This integration treats both `flagged` and `blocked` as blocked LiteLLM requests.

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

`mode: pre_call` tells LiteLLM to scan input before calling the model.

`default_on: true` applies the guardrail automatically.

`flag_only: false` sends `flagOnly: false` to ScanAPI, allowing F5 AI Security to return `blocked` for blocking guardrails.

`api_base` defaults to the US region shown above. You can also set `F5_GUARDRAILS_API_BASE` in the environment if you need a different F5 AI Security base URL.

## Notes

This example scans prompts before the model call. It does not scan model responses. To moderate both prompts and responses, add a separate LiteLLM post-call guardrail or scan model output in your application layer.
