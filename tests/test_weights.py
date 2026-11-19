"""Small standard-library smoke test: python -m unittest discover -s tests -v."""
import json
from pathlib import Path
import tempfile
import unittest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from weights import pack, read_manifest, restore


class SplitRestoreTest(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            (source / "nested").mkdir(parents=True)
            (source / "nested" / "tiny.bin").write_bytes(bytes(range(256)) * 8192)
            (source / "config.json").write_text('{"model_type":"test"}')
            dist = root / "dist"
            pack(type("Args", (), {"source": str(source), "out": str(dist), "chunk_mib": 1})())
            manifest = read_manifest(dist / "manifest.json")
            self.assertGreaterEqual(len(manifest["parts"]), 2)
            restored = root / "restored"
            restore(manifest, dist, restored)
            self.assertEqual((restored / "nested" / "tiny.bin").read_bytes(),
                             (source / "nested" / "tiny.bin").read_bytes())
            self.assertEqual((restored / "config.json").read_bytes(),
                             (source / "config.json").read_bytes())

    def test_tampered_part_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "file").write_bytes(b"abc")
            dist = root / "dist"
            pack(type("Args", (), {"source": str(source), "out": str(dist), "chunk_mib": 1})())
            manifest = read_manifest(dist / "manifest.json")
            part = dist / manifest["parts"][0]["name"]
            part.write_bytes(b"corrupted")
            with self.assertRaises(ValueError):
                restore(manifest, dist, root / "restore")


if __name__ == "__main__":
    unittest.main()
