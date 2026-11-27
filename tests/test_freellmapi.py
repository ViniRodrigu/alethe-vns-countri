import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "skills/alethe/scripts/freellmapi.py"


class FreeLLMAPIWorkerTest(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.status = 200
        self.delay = 0
        self.response = {
            "choices": [{"finish_reason": "stop", "message": {"content": "STATUS: DONE\nSUMMARY: Proposal"}}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 30},
        }
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fixture.requests.append((self.path, self.headers["Authorization"], body))
                time.sleep(fixture.delay)
                self.send_response(fixture.status)
                if fixture.status == 307:
                    self.send_header("Location", "/unexpected")
                self.end_headers()
                try:
                    self.wfile.write(json.dumps(fixture.response).encode())
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

            def log_message(self, *_args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        self.env = {
            **os.environ,
            "FREELLMAPI_API_KEY": "fixture-secret",
            "FREELLMAPI_MODEL": "fixture-model",
            "FREELLMAPI_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1",
        }

    def invoke(self, *extra, approved=True):
        args = [sys.executable, str(SCRIPT), "--input-tokens", "200", "--context-tokens", "1000",
                "--max-output-tokens", "200", "--timeout", "1"]
        if approved:
            args.append("--approved-context")
        return subprocess.run(
            [*args, *extra], input="Approved brief: correção".encode(), capture_output=True,
            env=self.env, timeout=10,
        )

    def test_proposal_and_usage(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.requests, [
            ("/v1/chat/completions", "Bearer fixture-secret", {
                "model": "fixture-model", "stream": False, "max_tokens": 200,
                "messages": [{"role": "user", "content": "Approved brief: correção"}],
            }),
        ])
        event = json.loads(result.stdout)
        self.assertEqual(event["result"], self.response["choices"][0]["message"]["content"])
        self.assertEqual(event["usage"], {"input_tokens": 120, "output_tokens": 30})

    def test_rate_limit_and_redirect_do_not_retry_or_echo_remote_body(self):
        self.response = {"error": "fixture-secret"}
        for status in (429, 307):
            with self.subTest(status=status):
                self.status = status
                result = self.invoke()
                self.assertEqual(result.returncode, 1)
                self.assertIn(f"HTTP {status}".encode(), result.stderr)
                self.assertNotIn(b"fixture-secret", result.stderr)
        self.assertEqual(len(self.requests), 2)

    def test_rejects_missing_approval_and_excess_context_before_request(self):
        self.assertNotEqual(self.invoke(approved=False).returncode, 0)
        self.assertNotEqual(self.invoke("--context-tokens", "300").returncode, 0)
        self.assertEqual(self.requests, [])

    def test_rejects_truncated_proposal(self):
        self.response["choices"][0]["finish_reason"] = "length"
        result = self.invoke()
        self.assertEqual(result.returncode, 1)
        self.assertFalse(result.stdout)
        self.assertIn(b"incomplete", result.stderr)

    def test_timeout(self):
        self.delay = 1.5
        result = self.invoke()
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"timed out", result.stderr)
        self.assertEqual(len(self.requests), 1)

    def test_runner_records_reported_usage_and_failure_kinds(self):
        with tempfile.TemporaryDirectory(prefix="free-api-") as directory:
            root = Path(directory)
            plan_path = root / "plan.json"
            policy = root / "policy.json"
            policy.write_text(json.dumps({"trust": {"workerEnv": ["FREELLMAPI_API_KEY"]}}), encoding="utf-8")
            for phase, delay, status, failure_kind in (
                ("proposal", 0, 200, None),
                ("rate-limit", 0, 429, "rate_limited"),
                ("timeout", 1.5, 200, "timed_out"),
            ):
                with self.subTest(phase=phase):
                    self.delay, self.status = delay, status
                    plan_path.write_text(json.dumps({
                        "version": 1, "strategy": "economy", "phase": phase,
                        "workers": [{
                            "id": "api", "provider": "custom", "mode": "write", "cwd": ".",
                            "command": sys.executable,
                            "args": [str(SCRIPT), "--approved-context", "--input-tokens", "200",
                                     "--context-tokens", "1000", "--max-output-tokens", "200",
                                     "--timeout", "1"],
                            "outputFormat": "json", "task": "Approved fixture brief.",
                        }],
                    }), encoding="utf-8")
                    run_dir = root / phase
                    runner_env = {**self.env, "ALETHE_ORCHESTRATE_HOME": str(root / "home")}
                    runner_env.pop("ALETHE_ORCHESTRATE_WORKER", None)
                    result = subprocess.run(
                        [sys.executable, str(SCRIPT.with_name("orchestrate.py")), "run", str(plan_path),
                         "--run-dir", str(run_dir), "--policy", str(policy)],
                        cwd=root, env=runner_env,
                        capture_output=True, timeout=20,
                    )
                    self.assertEqual(result.returncode, int(failure_kind is not None), result.stderr)
                    outcome_path = run_dir / "workers" / "api" / "outcome.json"
                    outcome = json.loads(outcome_path.read_text(encoding="utf-8"))
                    self.assertEqual(outcome.get("failureKind"), failure_kind)
                    if failure_kind is None:
                        self.assertEqual(outcome["metrics"]["usage"]["inputTokens"], 120)
                        self.assertEqual(outcome["metrics"]["usage"]["outputTokens"], 30)
