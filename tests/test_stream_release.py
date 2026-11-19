"""Run: python -m unittest discover -s tests -v"""
import importlib.util
from pathlib import Path
import tempfile
import unittest
import io
import hashlib
import zipfile

script = Path(__file__).resolve().parents[1] / "scripts" / "stream_release.py"
spec = importlib.util.spec_from_file_location("stream_release", script)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class StreamReleaseTests(unittest.TestCase):
    def test_split_and_restore_in_memory(self):
        originals = {"config.json": b'{"model_type":"test"}',
                     "nested/weight.bin": bytes(range(256)) * 16000}
        entries = [{"path": p, "size": len(b)} for p, b in originals.items()]
        def stream(entry):
            data = originals[entry["path"]]
            yield from (data[i:i + 4093] for i in range(0, len(data), 4093))
        with tempfile.TemporaryDirectory() as td:
            manifest = module.build_archive(entries, stream, td, chunk_mib=1)
            self.assertGreater(len(manifest["parts"]), 1)
            blob = b"".join((Path(td) / part["name"]).read_bytes() for part in manifest["parts"])
            self.assertEqual(hashlib.sha256(blob).hexdigest(), manifest["archive"]["sha256"])
            with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                for name, body in originals.items():
                    self.assertEqual(archive.read(name), body)


if __name__ == "__main__":
    unittest.main()
