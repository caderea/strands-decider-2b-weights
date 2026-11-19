# Strands Decider 2B weights mirror

Independent GitHub Releases mirror of [StrandsAgents/strands-decider-2B-qwen3.5-v1-2610](https://huggingface.co/StrandsAgents/strands-decider-2B-qwen3.5-v1-2610).

**This Strands checkpoint contains the LoRA adapter and decision head.** To run it without Hugging Face access, you also need its base model [Qwen/Qwen3.5-2B-Base](https://huggingface.co/Qwen/Qwen3.5-2B-Base), at the `base_revision` specified by the adapter. The two are distributed as separate GitHub Releases. The source files are mirrored unmodified, with upstream license and notices retained.

## Publish with GitHub Actions

Open [Publish Strands Decider 2B weights](https://github.com/caderea/strands-decider-2b-weights/actions/workflows/publish-weights.yml), click **Run workflow**, and run it twice:

1. Select `adapter`, leaving `revision` blank. This creates `strands-decider-2b-adapter-v1`.
2. Select `base`, leaving `revision` blank. The workflow reads `base_revision` from official adapter metadata and mirrors that matching Qwen base as `strands-decider-2b-base-v1`. It fails rather than silently selecting a potentially incompatible base if the revision cannot be found.

The process creates a draft GitHub Release, streams a pinned Hugging Face model revision into 1 GiB ZIP segments, uploads each segment and frees the runner's disk space, then uploads a SHA-256 manifest. It publishes the Release only once every required asset uploads.

A rerun with the same upstream revision can reuse draft assets after validating their size and SHA-256 hashes, and resumes interrupted Hugging Face streams with HTTP Range.

If the model is gated, configure an authorized GitHub Actions secret named `HF_TOKEN`. Never commit credentials.

## Download on another computer

Requires Git and Python 3.10+. No `huggingface_hub`, PyTorch or Git LFS is necessary **to download**.

```bash
git clone https://github.com/caderea/strands-decider-2b-weights.git
cd strands-decider-2b-weights

python3 scripts/weights.py download --tag strands-decider-2b-base-v1 --out ./models/Qwen3.5-2B-Base
python3 scripts/weights.py download --tag strands-decider-2b-adapter-v1 --out ./models/strands-decider-2b
```

The downloader verifies all chunks, reassembles and extracts the original model snapshot, and validates extracted file hashes. It stores the chunk cache separately for each tag and temporarily builds an archive during extraction, so allow ample disk space.

Releases must be published successfully before download commands will work. To load the model, install the official [strands-decider runtime](https://github.com/strands-labs/strands-decider) and configure its base-model path or cache appropriately. The mirror only provides the files, not an automatic runtime installation.

## Provenance and licensing

- Strands adapter and head: [StrandsAgents model](https://huggingface.co/StrandsAgents/strands-decider-2B-qwen3.5-v1-2610)
- Base weights: [Qwen/Qwen3.5-2B-Base](https://huggingface.co/Qwen/Qwen3.5-2B-Base)
- Both repositories describe Apache-2.0 licensing; review and retain their full upstream LICENSE / NOTICE files and model-specific attribution.
- The mirror is independent of Strands Agents, AWS, and Qwen.

Each Release manifest pins an immutable upstream revision and records SHA-256 checksums. Weights are stored as GitHub Release assets, **not** committed to Git history.
