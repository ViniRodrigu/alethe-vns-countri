"""FreeLLMAPI proposal worker for Alethe's custom CLI adapter."""

import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def completion_url(base_url):
    parsed = urlsplit(base_url)
    loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        not parsed.hostname
        or (parsed.scheme != "https" and not (parsed.scheme == "http" and loopback))
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path.rstrip("/").endswith("/v1")
    ):
        raise ValueError("Set a FreeLLMAPI /v1 base URL using HTTPS or loopback HTTP.")
    return parsed._replace(path=parsed.path.rstrip("/") + "/chat/completions").geturl()


def proposal_event(data):
    choice = data["choices"][0]
    if choice["finish_reason"] != "stop":
        raise ValueError("FreeLLMAPI returned an incomplete proposal.")
    content = choice["message"]["content"]
    if not isinstance(content, str) or not content.strip():
        raise ValueError("FreeLLMAPI returned an empty proposal.")
    event = {"type": "result", "result": content}
    usage = data.get("usage")
    if isinstance(usage, dict):
        counts = {"input_tokens": usage.get("prompt_tokens"), "output_tokens": usage.get("completion_tokens")}
        if all(type(count) is int and count >= 0 for count in counts.values()):
            event["usage"] = counts
            details = usage.get("prompt_tokens_details")
            cached = details.get("cached_tokens") if isinstance(details, dict) else None
            if type(cached) is int and 0 <= cached <= counts["input_tokens"]:
                counts["cached_input_tokens"] = cached
    return event


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approved-context", action="store_true", required=True)
    parser.add_argument("--input-tokens", type=int, required=True, help="Estimated input for the full request.")
    parser.add_argument("--context-tokens", type=int, required=True, help="Context window verified for the configured model.")
    parser.add_argument("--max-output-tokens", type=int, required=True)
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()
    if min(args.input_tokens, args.context_tokens, args.max_output_tokens, args.timeout) < 1:
        parser.error("Token limits and timeout must be positive.")
    if args.input_tokens + args.max_output_tokens > args.context_tokens:
        parser.error("Estimated input and requested output exceed the configured model context.")

    key = os.environ.get("FREELLMAPI_API_KEY", "").strip()
    model = os.environ.get("FREELLMAPI_MODEL", "").strip()
    base_url = os.environ.get("FREELLMAPI_BASE_URL", "").strip()
    if not key or not model or not base_url:
        parser.error("Set FREELLMAPI_API_KEY, FREELLMAPI_MODEL and FREELLMAPI_BASE_URL.")
    if model.lower() in {"auto", "fusion"} or model.lower().startswith(("auto:", "fusion:")):
        parser.error("Select an explicit model with a verified context window.")
    try:
        url = completion_url(base_url)
    except ValueError:
        parser.error("Set a FreeLLMAPI /v1 base URL using HTTPS or loopback HTTP.")

    task = sys.stdin.buffer.read().decode("utf-8")
    if not task.strip():
        parser.error("Provide the approved brief through stdin.")
    request = Request(
        url,
        data=json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": task}],
            "stream": False,
            "max_tokens": args.max_output_tokens,
        }).encode("utf-8"),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    try:
        opener = build_opener(ProxyHandler({}), NoRedirects())
        with opener.open(request, timeout=args.timeout) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError("FreeLLMAPI response exceeded the size limit.")
        event = proposal_event(json.loads(body))
    except HTTPError as error:
        print(f"FreeLLMAPI HTTP {error.code}", file=sys.stderr)
        error.close()
        return 1
    except (TimeoutError, URLError) as error:
        reason = error.reason if isinstance(error, URLError) else error
        print("FreeLLMAPI request timed out" if isinstance(reason, TimeoutError)
              else "FreeLLMAPI connection failed", file=sys.stderr)
        return 1
    except (ValueError, KeyError, IndexError, TypeError):
        print("FreeLLMAPI returned an invalid, incomplete or oversized proposal", file=sys.stderr)
        return 1

    print(json.dumps(event))
    return 0


if __name__ == "__main__":
    sys.exit(main())
