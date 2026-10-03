import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error


SCRIPT = Path(__file__).with_name("toke")
loader = importlib.machinery.SourceFileLoader("toke", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
cli = importlib.util.module_from_spec(spec)
loader.exec_module(cli)


class TokeTests(unittest.TestCase):
    def run_cli(self, args, stdin="", env=None, response=b'{"input_tokens": 17}', error=None, tty=False):
        stdout, stderr = io.StringIO(), io.StringIO()
        input_stream = io.StringIO(stdin)
        input_stream.isatty = lambda: tty
        environment = {"ANTHROPIC_API_KEY": "test-anthropic", "OPENAI_API_KEY": "test-openai"}
        environment.update(env or {})
        with (
            patch.dict(os.environ, environment, clear=True),
            patch.object(sys, "stdin", input_stream),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
            patch.object(cli.urllib.request, "urlopen", side_effect=error) as send,
        ):
            read = send.return_value.__enter__.return_value.read
            if isinstance(response, list):
                read.side_effect = response
            else:
                read.return_value = response
            try:
                code = cli.main(args)
            except SystemExit as exc:
                code = exc.code
        return code, stdout.getvalue(), stderr.getvalue(), send

    def test_anthropic_request_and_default_output(self):
        code, out, err, send = self.run_cli(["Hello 🌎\r\n"])
        self.assertEqual((code, out, err), (0, "Input tokens: 17\nProvider:     anthropic\nModel:        claude-opus-4-8\n", ""))
        request = send.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.anthropic.com/v1/messages/count_tokens")
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.get_header("X-api-key"), "test-anthropic")
        self.assertEqual(request.get_header("Anthropic-version"), "2023-06-01")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(json.loads(request.data), {
            "model": "claude-opus-4-8",
            "messages": [{"role": "user", "content": "Hello 🌎\r\n"}],
        })
        self.assertEqual(send.call_args.kwargs, {"timeout": 30})
        for flag in ("-q", "--quiet"):
            code, out, err, _ = self.run_cli([flag, "hello"], response=b'{"input_tokens": 1234}')
            self.assertEqual((code, out, err), (0, "1234\n", ""))

    def test_openai_request_and_json(self):
        code, out, err, send = self.run_cli(["-p", "openai", "--json", "--timeout", "4.5", "Hello"])
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out), {"provider": "openai", "model": "gpt-5.4", "input_tokens": 17})
        request = send.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.openai.com/v1/responses/input_tokens")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-openai")
        self.assertIsNone(request.get_header("X-api-key"))
        self.assertEqual(json.loads(request.data), {"model": "gpt-5.4", "input": "Hello"})
        self.assertEqual(send.call_args.kwargs, {"timeout": 4.5})

    def test_inputs_preserve_text_and_explicit_input_wins(self):
        value = "  héllo 🌎\r\n\n"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prompt.txt"
            path.write_bytes(value.encode("utf-8"))
            for args, stdin in [([], value), (["--file", "-"], value),
                                (["--file", str(path)], "ignored"),
                                (["--text", value], "ignored"), ([value], "ignored"),
                                ([""], "ignored")]:
                with self.subTest(args=args):
                    code, _, err, send = self.run_cli(args, stdin)
                    self.assertEqual((code, err), (0, ""))
                    actual = json.loads(send.call_args.args[0].data)["messages"][0]["content"]
                    self.assertEqual(actual, "" if args == [""] else value)

    def test_stdin_bytes_use_utf8_and_preserve_crlf(self):
        stream = io.TextIOWrapper(io.BytesIO("é\r\n".encode()), encoding="ascii")
        self.assertEqual(cli.read_text(cli.parser().parse_args([]), stream), "é\r\n")

    def test_environment_defaults_and_flag_precedence(self):
        env = {"TOKE_PROVIDER": "openai", "TOKE_OPENAI_MODEL": "custom-openai",
               "TOKE_ANTHROPIC_MODEL": "custom-anthropic"}
        for flags, provider, model in [([], "openai", "custom-openai"),
                                       (["-p", "anthropic"], "anthropic", "custom-anthropic"),
                                       (["-m", "explicit"], "openai", "explicit")]:
            with self.subTest(flags=flags):
                code, out, _, _ = self.run_cli([*flags, "--json", "Hello"], env=env)
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(out), {"provider": provider, "model": model, "input_tokens": 17})
                code, out, err, _ = self.run_cli([*flags, "Hello"], env=env, response=b'{"input_tokens": 1234}')
                self.assertEqual((code, out, err), (0, f"Input tokens: 1,234\nProvider:     {provider}\nModel:        {model}\n", ""))

    def test_invalid_arguments_do_not_send(self):
        for args in [["hello", "--file", "prompt"], ["--text", "a", "b"],
                     ["-p", "unknown", "text"], ["--model", "", "text"], ["--json", "--quiet", "text"],
                     *[["--timeout", value, "text"] for value in ["0", "-1", "nan", "inf", "oops"]]]:
            with self.subTest(args=args):
                code, out, err, send = self.run_cli(args)
                self.assertEqual((code, out), (2, ""))
                self.assertTrue(err)
                send.assert_not_called()
        code, _, _, send = self.run_cli(["hi"], env={"TOKE_PROVIDER": "invalid"})
        self.assertEqual(code, 2)
        send.assert_not_called()

    def test_missing_key_file_and_invalid_utf8_do_not_send(self):
        with tempfile.TemporaryDirectory() as directory:
            invalid = Path(directory) / "invalid.txt"
            invalid.write_bytes(b"\xff")
            for args, env in [(["hello"], {"ANTHROPIC_API_KEY": ""}),
                              (["-f", str(Path(directory) / "missing")], {}),
                              (["-f", str(invalid)], {})]:
                code, out, err, send = self.run_cli(args, env=env)
                self.assertEqual((code, out), (1, ""))
                self.assertTrue(err)
                send.assert_not_called()

    def test_terminal_without_input_has_actionable_error(self):
        code, out, err, send = self.run_cli([], tty=True)
        self.assertEqual((code, out), (1, ""))
        self.assertIn("pipe text", err)
        send.assert_not_called()

    def test_provider_errors_and_network_failures(self):
        errors = [
            (urllib.error.HTTPError("https://example.test", 401, "Unauthorized", {},
                                   io.BytesIO(b'{"error":{"message":"Invalid test-anthropic"}}')), "HTTP 401: Invalid [redacted]"),
            (urllib.error.HTTPError("https://example.test", 429, "Too Many Requests", {},
                                   io.BytesIO(b"<html>error</html>")), "HTTP 429"),
            (urllib.error.URLError("offline"), "request failed"),
            (TimeoutError("timed out"), "timed out"),
        ]
        for error, expected in errors:
            with self.subTest(error=error):
                code, out, err, _ = self.run_cli(["hello"], error=error)
                self.assertEqual((code, out), (1, ""))
                self.assertIn(expected, err)
                self.assertNotIn("test-anthropic", err)

    def test_malformed_responses_are_errors(self):
        for response in [b"not json", b"\xff", b"null", b"[]", b"{}",
                         b'{"input_tokens": true}', b'{"input_tokens": -1}',
                         b'{"input_tokens": "3"}', b'{"input_tokens": 2.5}']:
            with self.subTest(response=response):
                code, out, err, _ = self.run_cli(["hello"], response=response)
                self.assertEqual((code, out), (1, ""))
                self.assertTrue(err)
        code, out, err, _ = self.run_cli(["hello"], response=b'{"input_tokens": 0}')
        self.assertEqual((code, out, err), (0, "Input tokens: 0\nProvider:     anthropic\nModel:        claude-opus-4-8\n", ""))

    def test_executable_help_and_piped_failure_exit_status(self):
        help_result = subprocess.run([str(SCRIPT), "--help"], capture_output=True, text=True)
        self.assertEqual(help_result.returncode, 0)
        self.assertIn("--provider", help_result.stdout)
        result = subprocess.run([str(SCRIPT)], input="hello", capture_output=True,
                                text=True, env={"PATH": os.environ["PATH"]})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("set ANTHROPIC_API_KEY", result.stderr)

    def test_list_models_for_both_providers(self):
        for provider, mode in [(p, m) for p in ("anthropic", "openai")
                               for m in ("--list-models", "-m", "--model")]:
            with self.subTest(provider=provider, mode=mode), patch.object(cli, "read_text") as read:
                code, out, err, send = self.run_cli(
                    ["-p", provider, "--timeout", "5", mode], tty=True,
                    response=b'{"data":[{"id":"z-model"},{"id":"a-model"},{"id":"z-model"}]}',
                )
                self.assertEqual((code, out, err), (0, "a-model\nz-model\n", ""))
                read.assert_not_called()
                send.assert_called_once()
                request = send.call_args.args[0]
                self.assertEqual(request.full_url, f"https://api.{provider}.com/v1/models")
                self.assertEqual(request.method, "GET")
                self.assertIsNone(request.data)
                if provider == "anthropic":
                    self.assertEqual(request.get_header("X-api-key"), "test-anthropic")
                    self.assertEqual(request.get_header("Anthropic-version"), "2023-06-01")
                else:
                    self.assertEqual(request.get_header("Authorization"), "Bearer test-openai")
                self.assertEqual(send.call_args.kwargs, {"timeout": 5})

    def test_list_models_pagination_and_json(self):
        code, out, err, send = self.run_cli(["--list-models", "--json"], response=[
            b'{"data":[{"id":"z/model"}],"has_more":true,"last_id":"z/model"}',
            b'{"data":[{"id":"a-model"}],"has_more":false,"last_id":"a-model"}',
        ])
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out), {"provider": "anthropic", "models": ["a-model", "z/model"]})
        self.assertEqual(send.call_count, 2)
        self.assertEqual(send.call_args.args[0].full_url, "https://api.anthropic.com/v1/models?after_id=z%2Fmodel")

    def test_list_models_environment_and_empty_catalog(self):
        code, out, err, _ = self.run_cli(["-m", "--json"],
            env={"TOKE_PROVIDER": "openai", "TOKE_OPENAI_MODEL": ""}, response=b'{"data":[]}')
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out), {"provider": "openai", "models": []})
        code, out, err, _ = self.run_cli(["--list-models"], response=b'{"data":[]}')
        self.assertEqual((code, out, err), (0, "", ""))

    def test_list_models_invalid_arguments_and_missing_key(self):
        for flags in [["hello", "-m"], ["-m", "--text", "hello"], ["-m", "--file", "-"],
                      ["-m", "--list-providers"]]:
            code, out, _, send = self.run_cli(flags)
            self.assertEqual((code, out), (2, ""))
            send.assert_not_called()
        for flags in [["hello"], ["--text", "hello"], ["--file", "-"], ["--model", "any"]]:
            code, out, _, send = self.run_cli(["--list-models", *flags])
            self.assertEqual((code, out), (2, ""))
            send.assert_not_called()
        code, out, err, send = self.run_cli(["--list-models"], env={"ANTHROPIC_API_KEY": ""})
        self.assertEqual((code, out), (1, ""))
        self.assertIn("set ANTHROPIC_API_KEY", err)
        send.assert_not_called()

    def test_list_models_invalid_response_or_pagination(self):
        for response in [b'{}', b'null', b'{"data":{}}', b'{"data":[null]}',
                         b'{"data":[{"id":""}]}', b'{"data":[{"id":1}]}',
                         b'{"data":[],"has_more":"true"}',
                         b'{"data":[],"has_more":true}',
                         [b'{"data":[{"id":"x"}],"has_more":true,"last_id":"x"}'] * 2]:
            with self.subTest(response=response):
                code, out, err, _ = self.run_cli(["--list-models"], response=response)
                self.assertEqual((code, out), (1, ""))
                self.assertTrue(err)

    def test_list_models_later_page_failure_does_not_print_partial_results(self):
        code, out, err, _ = self.run_cli(["--list-models"], response=[
            b'{"data":[{"id":"x"}],"has_more":true,"last_id":"x"}',
            urllib.error.URLError("offline"),
        ])
        self.assertEqual((code, out), (1, ""))
        self.assertIn("request failed", err)


if __name__ == "__main__":
    unittest.main()
