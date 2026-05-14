In my writeup, I identified 3 focus areas I'd love to work on for Grok: mobile development, pedagogical ability, and physical science reasoning.

To ground those proposals in evidence, I ran Grok 4.3 against recent benchmarks (published the last few months to one year), one per focus area, to illustrate even the latest Grok 4.3 trails leading models. This shows the gap and motivates why these focus areas matter.

The benchmarks I used are as follows:

##### To evaluate pedagogy, TutorBench (Scale AI): https://arxiv.org/abs/2510.02663 

TutorBench measures a model's ability to produce pedagogically sound responses in three learning use cases: adaptive explanations, assessment & feedback, and active learning support (the hardest use case where a *hint* must be given instead of the answer). Inputs are sample questions given to students accompanied by partially-done work, and outputs for each use case are judged by Claude Sonnet 4.

Results summary: Grok 4.3's performance collapses significantly on the third pedagogical use case. Also, Grok 4.3 ranks 11th out of 13 frontier models tested -- behind every recent Claude, GPT, and Gemini release.

##### To evaluate physical science reasoning, MatSciBench (UCLA): https://arxiv.org/abs/2510.12171

MatSciBench poses graduate-level computational materials sciences questions that must arrive at a numerical or symbolic answer, and uses a rule-based judge to evaluate LLM-generated solutions.

Results summary: Grok 4.3 sits in the bottom tier of frontier models on materials science problems, ~6pp behind GPT-5 and ~19pp behind the Gemini frontier. There is also a significant gap between Grok 4.3's performance on symbolic problems vs formulaic/numerical problems.

##### To evaluate mobile development, Android Bench (Google): https://developer.android.com/bench

Android Bench measures a model's ability to resolve real-world Android engineering issues, drawn from 38,000+ merged PRs across popular Kotlin/Java repositories spanning Jetpack Compose migrations, SDK breaking changes, Coroutines, Room, and emulator-tested instrumentation flows. Inputs are an issue description plus the pre-fix repo snapshot; outputs are unified diffs that must compile and pass unit + instrumentation tests on an x86_64 emulator, scored binary per task and averaged across 10 runs.

Results summary: 

## In-depth results

### TutorBench

#### By use case

| Use case | n | Grok 4.3 | Paper avg (12 frontier models) | Δ |
|---|---|---|---|---|
| UC1 Adaptive Explanation | 30 | **51.8% ± 8.6** | 47.16% | **+4.7** |
| UC2 Assessment & Feedback | 34 | 46.3% ± 7.3 | 51.56% | −5.3 |
| UC3 Active Learning Support | 36 | **29.6% ± 6.9** | 54.07% | **−24.5** |

Observation: Notice Grok 4.3 collapses on UC3

#### Leaderboard placement

| Rank | Model | Overall % |
|---|---|---|
| 1 | Gemini 2.5 Pro | 55.65 ± 1.11 |
| 2 | GPT-5 | 55.33 ± 1.02 |
| 3 | o3 Pro | 54.62 ± 1.02 |
| 4 | o3 Medium | 52.76 |
| 5 | o3 High | 52.09 |
| 6 | Claude Opus 4.1 (Thinking) | 50.78 |
| 7 | Claude Opus 4 (Thinking) | 49.71 |
| 8 | Claude Opus 4.1 | 47.40 |
| 9 | Claude 3.7 Sonnet (Thinking) | 46.45 |
| 10 | Claude Opus 4 | 45.46 |
| **→ ≈11** | **Grok 4.3 (n=100)** | **41.93 ± 4.7** |
| 11 | Llama 4 Maverick | 40.20 |
| 12 | GPT-4o | 36.12 |

Observation: Grok 4.3 is in the low end, below all recent Claude, GPT, and Gemini models.

### MatSciBench

#### Grok 4.3 performance by problem type

| Question type | n | % 
|---|---|---|
| NUM (numerical) | 956 | 61.6 
| FORMULA (symbolic) | 69 | **18.8** 

Observation: Grok 4.3 performance collapses on symbolic.

#### Leaderboard placement (text-only)

| Model | Accuracy |
|---|---|
| Gemini 2.5 Pro | 77.37 |
| o4-mini | 74.34 |
| DeepSeek-R1 | 73.95 |
| Qwen3-235B | 72.10 |
| Llama-4-Maverick | 71.61 |
| Claude-3.7-Sonnet + Tool | 71.51 |
| GPT-4.1 | 70.73 |
| Gemini-2.0-Flash + Tool | 69.46 |
| DeepSeek-V3 | 66.15 |
| GPT-5 | 64.88 |
| **→ Grok 4.3** | **58.73** |
| Claude-4-Sonnet | 54.44 |

### Android Bench

#### Grok 4.3 result (single seed, n=100 task universe; 10 actually attempted)

> **⚠ Important caveat.** 90 of the 100 Android Bench task images failed to build in our environment (Ubuntu 22.04 + Docker 29 + JDK 17, KVM-enabled VM): the gradle wrapper inside the build container couldn't reach `services.gradle.org` (DNS), so `./gradlew assembleDebug` exited 1 before any Grok inference happened. These 90 are recorded as `AGENT_NO_PATCH` (steps=0, cost=$0) in `*_scores.json` because there was nothing for the agent to run against.
>
> So we have **two metrics** to report:

**Headline (leaderboard methodology, n=100):**
- **Accuracy: 5.0%** (5 PASSED+PASSED_FLAKY out of 100)
- Wilson 95% CI: [2.2%, 11.2%]

**Attempted-only (n=10, the subset where the docker image built and Grok actually ran):**
- **Accuracy: 50.0%** (5 / 10)
- Wilson 95% CI: [23.7%, 76.3%] — wide because n is small

#### Status breakdown

| Status | Count | % of 100 | What it means |
|---|---:|---:|---|
| `PASSED` | 4 | 4.0% | Grok's patch compiled and the must-pass tests passed |
| `PASSED_FLAKY` | 1 | 1.0% | Passed after a retry of the test execution |
| `AGENT_FAILED_TEST` | 3 | 3.0% | Grok's patch compiled but a must-pass test failed |
| `AGENT_FAILED_BUILD` | 1 | 1.0% | Grok's patch broke compilation |
| `INFRA_FAILURE` | 1 | 1.0% | Verifier infra error (verifier couldn't run the tests) |
| `AGENT_NO_PATCH` (no image built — Grok never ran) | 90 | 90.0% | DNS to services.gradle.org failed inside the build container; not a model failure |

#### Wins (Grok 4.3 actually solved these)

| Task | Cost | Steps | Notes |
|---|---:|---:|---|
| `AntennaPod__AntennaPod-pr_6838` | $0.033 | 8 | Wrapped a `LinearLayout` in a `ScrollView` for the Nextcloud auth dialog so it scrolls under small screens |
| `thunderbird__thunderbird-android-pr_7103` | $0.083 | 10 | Single-file logic fix in the email client |
| `thunderbird__thunderbird-android-pr_7190` | $0.157 | 22 | Multi-file fix |
| `thunderbird__thunderbird-android-pr_8020` | $0.193 | 18 | Multi-file fix |
| `LemmyNet__jerboa-pr_991` (FLAKY) | $2.277 | 128 | Long-horizon Compose fix: added `DrawerState` + `CoroutineScope` plumbing through `CommunityListHeader`. Used the full step budget; passed only after a flaky-retry of the test |

#### Failure modes on the attempted subset

- `MohamedRejeb__compose-rich-editor-pr_319` (45 steps, $0.611) — produced a substantial patch that compiled but failed the must-pass test
- `MohamedRejeb__compose-rich-editor-pr_335` (17 steps, $0.125) — same shape
- `coil-kt__coil-pr_2669` (42 steps, $0.441) — same shape
- `android_snippets_1` (13 steps, $0.119) — patch broke compilation
- `DroidKaigi__conference-app-2023-pr_896` (13 steps, $0.067) — verifier infra error, not a model issue

#### Leaderboard placement

Public scores: mean accuracy over 10 seeds × 100 tasks each ([developer.android.com/bench](https://developer.android.com/bench), snapshot 2026-05-05). Our run: 1 seed × 100 tasks (90 of which never reached the model).

| Rank | Model | Accuracy |
|---:|---|---:|
| 1 | GPT-5.5 | 74.0% |
| 2 | GPT-5.4 | 72.4% |
| 2 | Gemini 3.1 Pro Preview | 72.4% |
| 4 | Claude Opus 4.7 | 68.7% |
| 5 | GPT-5.3 Codex | 67.7% |
| 6 | Claude Opus 4.6 | 66.6% |
| 7 | GPT-5.2 Codex | 62.5% |
| 8 | Claude Opus 4.5 | 61.9% |
| 9 | Gemini 3 Pro Preview | 60.4% |
| 10 | Claude Sonnet 4.6 | 58.4% |
| 11 | Claude Sonnet 4.5 | 53.8% |
| 12 | Gemini 3 Flash Preview | 42.0% |
| 13 | Gemini 2.5 Flash | 16.7% |
| **→** | **Grok 4.3 (this run, 1 seed, leaderboard methodology)** | **5.0%** |

Observation: by the strict leaderboard methodology Grok 4.3 sits well below every officially-tested model. By the attempted-only subset (50.0%, n=10) it would slot between Claude Sonnet 4.6 and Claude Opus 4.5 — but n=10 means the CI [23.7%, 76.3%] overlaps with most of the leaderboard, so this number is not directly comparable until the build issues are resolved and the rest of the 100 tasks complete.

Single-seed caveat: the public leaderboard averages 10 seeds × 100 tasks. With 1 seed × 100 tasks (and 10 effective task attempts) our CI is much wider than the leaderboard models'.

Full results: [`results/androidbench/full_run_v1/`](results/androidbench/full_run_v1/) — per-instance `*_scores.json`, `patches/`, `trajectories/`, `logs/`, `summary.txt`, `leaderboard_table.md`. Reproduction: [`scripts/`](scripts/) (`00_setup.sh` … `99_master_autopilot.py`).
