# FreeLLMAPI proposal workers

The optional adapter targets [tashfeenahmed/freellmapi](https://github.com/tashfeenahmed/freellmapi)
using its [OpenAI-compatible API](https://github.com/tashfeenahmed/freellmapi/blob/main/docs/en/api/01-rest-api.md).
It sends one non-streaming `POST /v1/chat/completions` request with a bearer key. It reads
the approved worker brief from stdin and returns a proposal through the existing custom CLI
result format. It supplies no model tools and does not read source files or run the proposal.

## What to send

Send chores with an acceptance check the lead can run alone: a mechanical edit from an exact list
of changes, fixtures and test data, a test log summary, data extraction, text variants and
translation, or a first test draft from given names and assertions. Keep planning, decisions,
review, debugging, multi-file integration and framework API work with paid models. Treat the
proposal's `STATUS:` as a claim: apply it in a worktree whose worker has `allowedPaths` and
`acceptance` (see [operations.md](operations.md#operator-guards)), or apply it yourself and run
the check, and decide by the exit code.

## Configure a route

Set these variables in the shell or launcher that starts the skill:

| Variable | Value |
| --- | --- |
| `FREELLMAPI_BASE_URL` | The installation's API base URL ending in `/v1`. The documented local example is `http://localhost:3001/v1`. |
| `FREELLMAPI_API_KEY` | Unified key from the FreeLLMAPI instance, supplied through your local secret storage. |
| `FREELLMAPI_MODEL` | An explicit model ID that has capacity for the task. |

Use `GET /v1/models?available=true` with the same bearer key to check readiness. Confirm a
model's context, output and rate limits in the instance or [model catalog](https://freellmapi.co/models).
Limits and availability depend on the model, upstream account and configured keys. Allow
`FREELLMAPI_API_KEY` in the Alethe policy's `trust.workerEnv` for this run. Use a run-specific
policy and provide the variable only to the process launching the dedicated proposal wave.
Keep secrets out of plans, command arguments and handoffs. Approval must cover the FreeLLMAPI
instance and the upstream providers its configuration can send the brief to.

## Run a proposal

Use the local skill runner. The example runs from a Git checkout with a commit, with
`proposal.json` in its root. Use the absolute path to your installed adapter script.
The numbers are smoke-test examples. Replace them with the model's verified context limit,
the output cap needed for the task, and your estimate for the entire input including the
worker contract. The check uses your estimate and is independent of provider tokenization.

```json
{
  "version": 1,
  "strategy": "economy",
  "budget": { "maxCalls": 1 },
  "workers": [
    {
      "id": "proposal",
      "provider": "custom",
      "mode": "write",
      "isolate": true,
      "cwd": ".",
      "command": "python",
      "args": [
        "C:/path/to/skills/alethe/scripts/freellmapi.py",
        "--approved-context", "--input-tokens", "1000", "--context-tokens", "8192",
        "--max-output-tokens", "256", "--timeout", "45"
      ],
      "promptTransport": "stdin",
      "outputFormat": "json",
      "timeoutSeconds": 60,
      "task": "Smoke test with fictional data. Explain why Python lower() alone does not remove leading spaces. Return a concise result."
    }
  ]
}
```

For this plan, an optional run-specific policy file named `proposal-policy.json` can contain
`{"trust":{"workerEnv":["FREELLMAPI_API_KEY"]}}`. Use these commands:

```text
python skills/alethe/scripts/orchestrate.py dry-run proposal.json --policy proposal-policy.json
python skills/alethe/scripts/orchestrate.py run proposal.json --policy proposal-policy.json
python skills/alethe/scripts/orchestrate.py report
```

The plan uses a disposable worktree because the runner requires write mode for unclassified
custom adapters. For a real proposal, use `taskFile` in place of `task` when the approved
brief is long. Include the authorized code excerpts or sanitized logs, the deliverable and
acceptance checks. Read `outcome.json` and the summary first, then open `result.md` for the
proposal as needed. Validate a returned patch with `git apply --check` and execute the checks
locally before accepting. The report counts tokens returned in API usage where available.

On HTTP 429, timeout, or an unusable proposal, record the outcome and stay within a two-call
orchestration budget for the unit. Use a second wave for one targeted correction or for
reassignment. Account for retries or fallback performed internally by the router if its own
usage records are available, since one Alethe call can take more than one upstream attempt.
