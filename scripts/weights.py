#!/usr/bin/env python3
"""Package, verify and retrieve an unmodified Hugging Face snapshot via GitHub Releases."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import urllib.request
import zipfile

REPO = "caderea/strands-decider-2b-weights"
BUFFER = 4 * 1024 * 1024


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(BUFFER), b""):
            h.update(block)
    return h.hexdigest()


def files_under(root):
    for p in sorted(root.rglob("*")):
        if p.is_symlink():
            raise ValueError(f"Symlinks are unsupported: {p}")
        if p.is_file():
            yield p


def pack(args):
    root = Path(args.source).resolve()
    if not root.is_dir():
        raise ValueError("Source directory does not exist")
    out = Path(args.out).resolve()
    if out == root or root in out.parents:
        raise ValueError("Output must be outside the source directory")
    out.mkdir(parents=True, exist_ok=True)
    model_files = list(files_under(root))
    if not model_files:
        raise ValueError("No source files")
    entries = [{"path": p.relative_to(root).as_posix(), "size": p.stat().st_size,
                "sha256": digest(p)} for p in model_files]
    archive = out / "weights.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as z:
        for p in model_files:
            z.write(p, arcname=p.relative_to(root).as_posix())
    archive_hash = hashlib.sha256()
    chunks = []
    chunk_bytes = args.chunk_mib * 1024 * 1024
    if chunk_bytes < 1 or chunk_bytes >= 2 * 1024**3:
        raise ValueError("Chunk size must be between 1 byte and 2 GiB (exclusive)")
    with archive.open("rb") as src:
        i = 0
        while True:
            part = out / f"weights.zip.part{i:04d}"
            h = hashlib.sha256()
            size = 0
            with part.open("wb") as dst:
                while size < chunk_bytes:
                    block = src.read(min(BUFFER, chunk_bytes - size))
                    if not block:
                        break
                    dst.write(block)
                    h.update(block)
                    archive_hash.update(block)
                    size += len(block)
            if not size:
                part.unlink()
                break
            chunks.append({"name": part.name, "size": size, "sha256": h.hexdigest()})
            i += 1
    manifest = {"format": 1, "source": "StrandsAgents/strands-decider-2B-qwen3.5-v1-2610",
                "archive": {"name": archive.name, "size": archive.stat().st_size,
                            "sha256": archive_hash.hexdigest()},
                "files": entries, "parts": chunks}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    archive.unlink()
    print(f"Packaged {len(model_files)} files into {len(chunks)} parts at {out}")


def read_manifest(path):
    m = json.loads(Path(path).read_text())
    if m.get("format") != 1:
        raise ValueError("Unsupported manifest format")
    return m


def verify_part(p, item):
    return p.is_file() and p.stat().st_size == item["size"] and digest(p) == item["sha256"]


def restore(manifest, parts_dir, out):
    out = Path(out).resolve()
    parts_dir = Path(parts_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pplx-restore-") as tmp:
        archive = Path(tmp) / "weights.zip"
        h = hashlib.sha256()
        with archive.open("wb") as dst:
            for item in manifest["parts"]:
                name = item["name"]
                if Path(name).name != name:
                    raise ValueError("Invalid part name")
                p = parts_dir / name
                if not verify_part(p, item):
                    raise ValueError(f"Missing or invalid part: {p}")
                with p.open("rb") as src:
                    for block in iter(lambda: src.read(BUFFER), b""):
                        dst.write(block)
                        h.update(block)
        expected = manifest["archive"]
        if archive.stat().st_size != expected["size"] or h.hexdigest() != expected["sha256"]:
            raise ValueError("Reassembled archive checksum mismatch")
        with zipfile.ZipFile(archive) as z:
            actual = {i.filename for i in z.infolist() if not i.is_dir()}
            expected_names = {f["path"] for f in manifest["files"]}
            if actual != expected_names:
                raise ValueError("Archive file list mismatch")
            for item in manifest["files"]:
                name = item["path"]
                dest = (out / name).resolve()
                if not dest.is_relative_to(out):
                    raise ValueError("Unsafe path in archive")
                dest.parent.mkdir(parents=True, exist_ok=True)
                with z.open(name) as src, dest.open("wb") as dst:
                    shutil.copyfileobj(src, dst, BUFFER)
                if dest.stat().st_size != item["size"] or digest(dest) != item["sha256"]:
                    dest.unlink(missing_ok=True)
                    raise ValueError(f"Extracted file mismatch: {name}")
    print(f"Successfully restored {len(manifest['files'])} files to {out}")


def download_url(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": "pplx-weight-mirror/1.0"})
    temp = dest.with_name(dest.name + ".tmp")
    try:
        with urllib.request.urlopen(req, timeout=90) as response, temp.open("wb") as dst:
            shutil.copyfileobj(response, dst, BUFFER)
        temp.replace(dest)
    finally:
        temp.unlink(missing_ok=True)


def download(args):
    from urllib.parse import quote
    tag = quote(args.tag, safe="")
    base = f"https://github.com/{REPO}/releases/download/{tag}"
    with tempfile.TemporaryDirectory(prefix="pplx-parts-") as tmp:
        parts = Path(tmp)
        manifest_path = parts / "manifest.json"
        download_url(base + "/manifest.json", manifest_path)
        m = read_manifest(manifest_path)
        # Persist verified parts next to output for interrupted download recovery.
        cache = Path(args.cache).resolve() if args.cache else Path(args.out).resolve().parent / ".strands-parts"
        cache.mkdir(parents=True, exist_ok=True)
        for item in m["parts"]:
            name = item["name"]
            if Path(name).name != name:
                raise ValueError("Unsafe release asset name")
            p = cache / name
            if not verify_part(p, item):
                print("Downloading", name, flush=True)
                download_url(base + "/" + quote(name), p)
                if not verify_part(p, item):
                    p.unlink(missing_ok=True)
                    raise ValueError(f"Checksum mismatch: {name}")
        restore(m, cache, args.out)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="action", required=True)
    a = sub.add_parser("pack")
    a.add_argument("source")
    a.add_argument("--out", required=True)
    a.add_argument("--chunk-mib", type=int, default=1024)
    a.set_defaults(func=pack)
    a = sub.add_parser("restore")
    a.add_argument("manifest")
    a.add_argument("--parts-dir", required=True)
    a.add_argument("--out", required=True)
    a.set_defaults(func=lambda x: restore(read_manifest(x.manifest), x.parts_dir, x.out))
    a = sub.add_parser("download")
    a.add_argument("--tag", required=True)
    a.add_argument("--out", required=True)
    a.add_argument("--cache")
    a.set_defaults(func=download)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
