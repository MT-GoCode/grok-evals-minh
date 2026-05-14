# Android Bench — Grok 4.3 detailed results

> Last refreshed by `scripts/99_master_autopilot.py:update_readme_section()`.

## Headline numbers

> **⚠ Important caveat.** 61/100 of the Android Bench task images failed to build in our environment (Ubuntu 22.04 + Docker 29 + JDK 17, KVM-enabled VM): the gradle wrapper inside the build container couldn't reach `services.gradle.org` (DNS) on the default bridge network for those images. They're recorded as `AGENT_NO_PATCH` (steps=0, cost=$0) in `*_scores.json` because there was nothing for the agent to run against. These are **not** model failures.
>
> So we report **two metrics**:

**Headline (leaderboard methodology, n=100):**
- **Accuracy: 23.0%** (23 PASSED+PASSED_FLAKY out of 100)
- Wilson 95% CI: [15.8%, 32.2%]

**Attempted-only (n=37, the subset where the docker image built and Grok actually ran):**
- **Accuracy: 62.2%** (23 / 37)
- Wilson 95% CI: [46.1%, 75.9%] — wide because n is small (single seed)

## Status breakdown

| Status | Count | % of 100 | What it means |
|---|---:|---:|---|
| `AGENT_NO_PATCH (no image built — Grok never ran)` | 61 | 61.0% | No docker image was built — Grok never ran (not a model failure) |
| `PASSED` | 22 | 22.0% | Grok's patch compiled and the must-pass tests passed |
| `AGENT_FAILED_TEST` | 11 | 11.0% | Grok's patch compiled but a must-pass test failed |
| `INFRA_FAILURE` | 2 | 2.0% | Verifier infra error |
| `INFRA_FAILURE_AGENT` | 2 | 2.0% |  |
| `PASSED_FLAKY` | 1 | 1.0% | Passed after a retry of the test execution |
| `AGENT_FAILED_BUILD` | 1 | 1.0% | Grok's patch broke compilation |

## Wins (Grok 4.3 actually solved these)

| Task | Cost | Steps | Notes |
|---|---:|---:|---|
| `AntennaPod__AntennaPod-pr_6838` | $0.033 | 8 |  |
| `Automattic__pocket-casts-android-pr_3970` | $0.171 | 16 |  |
| `CatimaLoyalty__Android-pr_1524` | $0.096 | 14 |  |
| `LemmyNet__jerboa-pr_1068` | $0.537 | 52 |  |
| `LemmyNet__jerboa-pr_1114` | $0.200 | 22 |  |
| `LemmyNet__jerboa-pr_1198` | $0.853 | 54 |  |
| `LemmyNet__jerboa-pr_809` | $0.235 | 23 |  |
| `LemmyNet__jerboa-pr_868` | $0.111 | 17 |  |
| `LemmyNet__jerboa-pr_894` | $0.048 | 9 |  |
| `LemmyNet__jerboa-pr_941` | $0.104 | 14 |  |
| `LemmyNet__jerboa-pr_947` | $0.106 | 15 |  |
| `LemmyNet__jerboa-pr_985` | $0.251 | 18 |  |
| `LemmyNet__jerboa-pr_991` | $2.277 | 128 | FLAKY (passed only after test retry) |
| `MohamedRejeb__compose-rich-editor-pr_363` | $0.804 | 51 |  |
| `MohamedRejeb__compose-rich-editor-pr_379` | $0.286 | 19 |  |
| `MohamedRejeb__compose-rich-editor-pr_403` | $1.273 | 68 |  |
| `MohamedRejeb__compose-rich-editor-pr_445` | $2.221 | 59 |  |
| `MohamedRejeb__compose-rich-editor-pr_523` | $0.559 | 32 |  |
| `android__nowinandroid-pr_553` | $0.092 | 13 |  |
| `android__nowinandroid-pr_720` | $0.522 | 31 |  |
| `thunderbird__thunderbird-android-pr_7103` | $0.083 | 10 |  |
| `thunderbird__thunderbird-android-pr_7190` | $0.157 | 22 |  |
| `thunderbird__thunderbird-android-pr_8020` | $0.193 | 18 |  |

## Failures on the attempted subset

| Task | Status | Cost | Steps |
|---|---|---:|---:|
| `AlphaWallet__alpha-wallet-android-pr_3329` | AGENT_FAILED_TEST | $0.118 | 17 |
| `Automattic__pocket-casts-android-pr_1114` | AGENT_FAILED_TEST | $0.116 | 15 |
| `Automattic__pocket-casts-android-pr_757` | AGENT_FAILED_TEST | $0.453 | 34 |
| `CatimaLoyalty__Android-pr_1588` | INFRA_FAILURE | $0.306 | 29 |
| `DroidKaigi__conference-app-2023-pr_896` | INFRA_FAILURE | $0.067 | 13 |
| `LemmyNet__jerboa-pr_1122` | AGENT_FAILED_TEST | $2.214 | 127 |
| `LemmyNet__jerboa-pr_946` | AGENT_FAILED_TEST | $0.282 | 17 |
| `MohamedRejeb__compose-rich-editor-pr_319` | AGENT_FAILED_TEST | $0.611 | 45 |
| `MohamedRejeb__compose-rich-editor-pr_335` | AGENT_FAILED_TEST | $0.125 | 17 |
| `MohamedRejeb__compose-rich-editor-pr_357` | AGENT_FAILED_TEST | $0.409 | 24 |
| `MohamedRejeb__compose-rich-editor-pr_367` | AGENT_FAILED_TEST | $0.566 | 28 |
| `airbnb__lottie-android-pr_2427` | AGENT_FAILED_TEST | $0.234 | 20 |
| `android_snippets_1` | AGENT_FAILED_BUILD | $0.118 | 13 |
| `coil-kt__coil-pr_2669` | AGENT_FAILED_TEST | $0.441 | 42 |

## Reproducing this run from scratch

**Hardware**: x86_64 Ubuntu 22.04+ VM, KVM-enabled (`/dev/kvm` writable), ≥32 GB RAM with a 32 GB swapfile, ≥600 GB free disk. Cloud VMs work; ARM64 doesn't (no Android x86 emulator).

**API keys** in `~/.env`:
```bash
XAI_API_KEY=sk-xai-...
GITHUB_PAT=ghp-...   # optional, dodges GitHub rate limits during repo-base builds
```

**One-shot reproduction**:
```bash
git clone git@github.com:MT-GoCode/grok-evals-minh.git && cd grok-evals-minh
bash scripts/run_all_remote.sh full_run_v1 4
```

This calls each of the numbered scripts in order; everything is idempotent and resumable. Final results land in `results/androidbench/full_run_v1/` and the main README's `### Android Bench` section above is auto-rewritten.

### What each script does

| Script | Purpose |
|---|---|
| `scripts/00_setup.sh` | apt deps (docker, qemu-kvm), install uv, clone vendor `android-bench/android-bench` into `vendor/AndroidBench`, create Python 3.14 venv + install deps |
| `scripts/10_setup_base.sh` | vendor's `utils.setup` — oracle agent + dataset summary |
| `scripts/20_build_images.sh` | **Two-pass build.** Pass 1: vendor's `generate_docker_images.py` builds the base + ~32 repo-base images. Pass 2: `rebuild_with_dns.sh` rebuilds the 100 task layers with `docker build --network=host` (gradle wrapper can't resolve `services.gradle.org` on the default bridge network in many cloud-VM docker setups). |
| `scripts/30_inference.sh` | `harness.inference.androidbench --workers N --model xai/grok-4.3 --skip-existing` — Grok generates patches, parallel across N workers |
| `scripts/40_verify.sh` | vendor's verifier — applies each patch, runs unit + instrumentation tests in another docker container, scores `PASSED / AGENT_FAILED_TEST / ...` |
| `scripts/50_aggregate.py` | Wilson 95% CI, status breakdown, leaderboard table, JSONL in `grok-evals` format |
| `scripts/99_master_autopilot.py` | Full unattended runner. Waits for build, then per-task inference + bulk verify, with `git push` checkpoints every N tasks. Stops on xAI balance-exhausted. |
| `scripts/35_inference_budgeted.py` | Sequential inference with a hard $-cap (used during exploratory runs). |
| `scripts/auto_inference_loop.sh` | Re-runs inference every 15 min as new task images come online from a parallel rebuild. |
| `scripts/rebuild_with_dns.sh` | Parallel `docker build --network=host` for any task images that don't yet exist — used as the second build pass and as a recovery tool. |
| `scripts/finalize_results.py` | Merges all `*_scores.json` files (the verifier writes new files per filter), restores any statuses overwritten by a verifier startup pass, runs `50_aggregate.py`, refreshes both READMEs. |

## Caveats & things to know

1. **Verifier `--skip-existing` quirk.** The vendor's verifier overwrites the consolidated `0_to_99_scores.json` with placeholder `AGENT_NO_PATCH` rows at startup, then leaves `--skip-existing` rows at the placeholder. `finalize_results.py` works around this by merging all per-invocation `*_scores.json` files and never letting a fresh placeholder overwrite a real prior status.
2. **Docker `--network=host`** is required for many cloud-VM docker setups due to DNS-resolution failure inside the default bridge network. Bare-metal / clean-docker environments may not need it.
3. **Single-seed.** This run is 1 seed × the buildable subset; the public leaderboard reports the mean of 10 seeds × all 100 tasks. The Wilson 95% CI here is correspondingly wider (~±15pp at n=37 vs ~±5pp on the leaderboard).
4. **Cost cap.** Each task is hard-capped at $10 of model spend by `harness/inference/androidbench.yaml`. Median observed per-task cost in this run was ~$0.17.

## Where the raw data is

- `results/androidbench/full_run_v1/summary.txt` — text headline + status breakdown
- `results/androidbench/full_run_v1/leaderboard_table.md` — drop-in placement table
- `results/androidbench/full_run_v1/by_status.json` — `{status: [instance_ids]}`
- `vendor/AndroidBench/out/full_run_v1/*_scores.json` — per-instance verifier output
- `vendor/AndroidBench/out/full_run_v1/patches/<task>.patch` — the diff Grok produced
- `vendor/AndroidBench/out/full_run_v1/trajectories/<task>.json` — full agent transcript (commands + model responses + cost/token accounting)
- `results/androidbench/<ts>.jsonl` + `<ts>.summary.json` — `grok-evals`-format aggregates
