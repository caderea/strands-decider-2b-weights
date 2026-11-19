"""Unit tests for interrupted HTTP streams and exact Range resume."""
import importlib.util
from pathlib import Path
import hashlib
import io
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "stream_release", Path(__file__).resolve().parents[1] / "scripts" / "stream_release.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Response:
    def __init__(self, code, headers, data, interrupt=False):
        self.status_code = code
        self.headers = headers
        self.data = data
        self.interrupt = interrupt

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk):
        import requests
        if self.interrupt:
            yield self.data[:6]
            raise requests.exceptions.ChunkedEncodingError("simulated disconnect")
        yield self.data


class Session:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.headers_seen = []

    def get(self, url, **kwargs):
        self.headers_seen.append(kwargs["headers"])
        return next(self.responses)


class RangeResumeTests(unittest.TestCase):
    def test_recovers_at_correct_offset(self):
        s = Session([Response(200, {}, b"abcdef", True),
                     Response(206, {"Content-Range": "bytes 6-9/10"}, b"ghij")])
        result = b"".join(module.iter_resumable(s, "https://example.test/x", 10, sleep=lambda _: None))
        self.assertEqual(result, b"abcdefghij")
        self.assertEqual(s.headers_seen[1]["Range"], "bytes=6-")

    def test_refuses_server_that_ignores_range(self):
        s = Session([Response(200, {}, b"abcdef", True),
                     Response(200, {}, b"abcdefghij")])
        with self.assertRaisesRegex(RuntimeError, "ignored Range"):
            b"".join(module.iter_resumable(s, "https://example.test/x", 10, sleep=lambda _: None))

    def test_refuses_wrong_range_start(self):
        s = Session([Response(200, {}, b"abcdef", True),
                     Response(206, {"Content-Range": "bytes 3-9/10"}, b"defghij")])
        with self.assertRaisesRegex(RuntimeError, "Invalid resume"):
            b"".join(module.iter_resumable(s, "https://example.test/x", 10, sleep=lambda _: None))

    def test_reuses_only_matching_draft_asset(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "weights.zip.part0000"
            p.write_bytes(b"verified bytes")
            metadata = {"name": p.name, "size": p.stat().st_size,
                        "digest": "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()}
            with patch.object(module.subprocess, "run") as cmd:
                module.upload_or_reuse(p, "owner/repo", "tag", {p.name: metadata})
                cmd.assert_not_called()
            self.assertFalse(p.exists())

    def test_rejects_mismatched_asset(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "weights.zip.part0000"
            p.write_bytes(b"verified bytes")
            with self.assertRaisesRegex(ValueError, "does not match"):
                module.upload_or_reuse(p, "owner/repo", "tag", {
                    p.name: {"size": p.stat().st_size, "digest": "sha256:" + "0" * 64}
                })


if __name__ == "__main__":
    unittest.main()
