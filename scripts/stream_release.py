#!/usr/bin/env python3
"""Stream a pinned Hugging Face model snapshot into split GitHub Release assets."""
import argparse
import hashlib
import io
import json
import os
import subprocess
import time
from pathlib import Path
import zipfile


class ChunkWriter(io.RawIOBase):
    def __init__(self, out, chunk_bytes, on_part=None):
        self.out = Path(out)
        self.on_part = on_part
        self.limit = chunk_bytes
        self.total = 0
        self.parts = []
        self.current = None
        self.part_size = 0
        self.part_hash = None
        self.archive_hash = hashlib.sha256()
        self.finalized = False

    def writable(self):
        return True

    def tell(self):
        return self.total

    def _start(self):
        name = f"weights.zip.part{len(self.parts):04d}"
        self.current = (self.out / name).open("wb")
        self.current_name = name
        self.part_size = 0
        self.part_hash = hashlib.sha256()

    def _finish(self):
        if self.current is None:
            return
        self.current.close()
        item = {"name": self.current_name, "size": self.part_size,
                "sha256": self.part_hash.hexdigest()}
        if self.on_part:
            self.on_part(self.out / self.current_name)
        self.parts.append(item)
        self.current = None

    def write(self, b):
        if self.finalized:
            raise ValueError("Already finalized")
        view = memoryview(b)
        length = len(view)
        offset = 0
        while offset < length:
            if self.current is None:
                self._start()
            n = min(length - offset, self.limit - self.part_size)
            block = view[offset:offset + n]
            self.current.write(block)
            self.archive_hash.update(block)
            self.part_hash.update(block)
            self.total += n
            self.part_size += n
            offset += n
            if self.part_size == self.limit:
                self._finish()
        return length

    def flush(self):
        if self.current is not None:
            self.current.flush()

    def finalize(self):
        self._finish()
        self.finalized = True


def build_archive(entries, stream_file, out, chunk_mib=1024, on_part=None, source='StrandsAgents/strands-decider-2B-qwen3.5-v1-2610'):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    limit = chunk_mib * 1024 * 1024
    if not 0 < limit < 2 * 1024**3:
        raise ValueError("Chunk size must be below 2 GiB")
    writer = ChunkWriter(out, limit, on_part=on_part)
    records = []
    with zipfile.ZipFile(writer, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for entry in entries:
            path = entry["path"]
            if not path or path.startswith("/") or ".." in Path(path).parts or "\\" in path:
                raise ValueError(f"Unsafe path: {path}")
            sha = hashlib.sha256()
            size = 0
            info = zipfile.ZipInfo(path)
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o644 << 16
            with archive.open(info, "w", force_zip64=True) as dest:
                for block in stream_file(entry):
                    dest.write(block)
                    sha.update(block)
                    size += len(block)
            if entry.get("size") is not None and size != entry["size"]:
                raise ValueError(f"Size mismatch for {path}")
            if entry.get("sha256") and sha.hexdigest() != entry["sha256"]:
                raise ValueError(f"SHA256 mismatch for {path}")
            records.append({"path": path, "size": size, "sha256": sha.hexdigest()})
            print(f"Packaged {path} ({size} bytes)", flush=True)
    writer.finalize()
    return {"format": 1, "source": source,
            "archive": {"name": "weights.zip", "size": writer.total,
                        "sha256": writer.archive_hash.hexdigest()},
            "files": records, "parts": writer.parts}



def iter_resumable(session, url, total_size, block_size=4 * 1024 * 1024, max_retries=12, sleep=time.sleep):
    """Yield exact upstream bytes, resuming interrupted HTTP responses with Range."""
    import requests
    offset = 0
    failures = 0
    while total_size is None or offset < total_size:
        headers = {"Accept-Encoding": "identity"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        try:
            with session.get(url, stream=True, timeout=(30, 300), headers=headers) as response:
                if offset:
                    if response.status_code != 206:
                        raise RuntimeError(f"Server ignored Range request at byte {offset}: HTTP {response.status_code}")
                    content_range = response.headers.get("Content-Range", "")
                    if not content_range.startswith(f"bytes {offset}-"):
                        raise RuntimeError(f"Invalid resume Content-Range: {content_range}")
                elif response.status_code not in (200, 206):
                    response.raise_for_status()
                elif response.status_code == 206 and not response.headers.get("Content-Range", "").startswith("bytes 0-"):
                    raise RuntimeError("Unexpected initial Content-Range")
                for block in response.iter_content(block_size):
                    if not block:
                        continue
                    if total_size is not None and offset + len(block) > total_size:
                        raise RuntimeError("Server returned more bytes than upstream manifest")
                    offset += len(block)
                    failures = 0
                    yield block
                if total_size is None or offset == total_size:
                    return
                raise requests.exceptions.ChunkedEncodingError(f"Stream ended early at {offset}/{total_size}")
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout,
                requests.exceptions.ChunkedEncodingError) as exc:
            failures += 1
            if failures > max_retries:
                raise RuntimeError(f"Download exhausted retries at byte {offset}") from exc
            delay = min(30, 2 ** min(failures, 5))
            print(f"Interrupted at byte {offset}; retry {failures}/{max_retries} in {delay}s: {exc}", flush=True)
            sleep(delay)
    return


def find_release(repo, tag):
    """Find a draft via list endpoint (GET-by-tag may return 404 for drafts)."""
    page = 1
    while True:
        raw = subprocess.check_output(
            ["gh", "api", f"repos/{repo}/releases?per_page=100&page={page}"], text=True)
        batch = json.loads(raw)
        for release in batch:
            if release.get("tag_name") == tag:
                return release
        if len(batch) < 100:
            return None
        page += 1


def release_assets(repo, tag):
    release = find_release(repo, tag)
    if release is None or not release.get("draft"):
        raise ValueError("Expected an existing draft release; refusing to overwrite a published release")
    return {a["name"]: a for a in release.get("assets", [])}


def upload_or_reuse(part, repo, tag, prior):
    name = part.name
    existing = prior.get(name)
    if existing is not None:
        expected = "sha256:" + file_sha256(part)
        if existing.get("size") != part.stat().st_size or existing.get("digest") != expected:
            raise ValueError(f"Existing asset {name} does not match locally generated bytes; refusing overwrite")
        print(f"Reusing previously uploaded verified asset {name}", flush=True)
    else:
        print(f"Uploading new asset {name}", flush=True)
        subprocess.run(["gh", "release", "upload", tag, str(part), "--repo", repo], check=True)
    part.unlink()


def file_sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as src:
        for block in iter(lambda: src.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="StrandsAgents/strands-decider-2B-qwen3.5-v1-2610")
    parser.add_argument("--revision", default="main")
    parser.add_argument("--out", default="dist")
    parser.add_argument("--chunk-mib", type=int, default=1024)
    parser.add_argument("--release-tag", help="Upload chunks to draft release, deleting local copies")
    args = parser.parse_args()
    from huggingface_hub import HfApi, hf_hub_url
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    api = HfApi()
    revision = api.model_info(args.repo, revision=args.revision).sha
    tree = list(api.list_repo_tree(args.repo, recursive=True, revision=revision))
    entries = []
    for item in tree:
        if item.__class__.__name__ != "RepoFile":
            continue
        lfs = getattr(item, "lfs", None)
        entries.append({"path": item.path, "size": item.size,
                        "sha256": (lfs or {}).get("sha256") if isinstance(lfs, dict) else None})
    entries.sort(key=lambda e: e["path"])
    if not entries:
        raise RuntimeError("No model files found")
    print(f"Pinned revision: {revision}; {len(entries)} files", flush=True)
    session = requests.Session()
    if os.environ.get("HF_TOKEN"):
        session.headers.update({"Authorization": "Bearer " + os.environ["HF_TOKEN"]})
    retry = Retry(total=5, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retry))

    def stream(entry):
        url = hf_hub_url(args.repo, entry["path"], revision=revision)
        yield from iter_resumable(session, url, entry.get("size"))

    prior = release_assets(os.environ["GITHUB_REPOSITORY"], args.release_tag) if args.release_tag else {}

    def upload_part(part):
        upload_or_reuse(part, os.environ["GITHUB_REPOSITORY"], args.release_tag, prior)

    manifest = build_archive(entries, stream, args.out, args.chunk_mib,
                             on_part=upload_part if args.release_tag else None,
                             source=args.repo)
    manifest["revision"] = revision
    (Path(args.out) / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    if args.release_tag:
        upload_or_reuse(Path(args.out) / "manifest.json", os.environ["GITHUB_REPOSITORY"], args.release_tag, prior)
    print(f"Wrote {len(manifest['parts'])} chunks; total {manifest['archive']['size']} bytes", flush=True)


if __name__ == "__main__":
    main()
