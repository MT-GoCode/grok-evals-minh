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

#### Grok 4.3 result (single seed, n=100 task universe; 35 actually attempted)

> **⚠ Important caveat.** 61/100 of the Android Bench task images failed to build in our environment (Ubuntu 22.04 + Docker 29 + JDK 17, KVM-enabled VM): the gradle wrapper inside the build container couldn't reach `services.gradle.org` (DNS) on the default bridge network for those images. They're recorded as `AGENT_NO_PATCH` (steps=0, cost=$0) in `*_scores.json` because there was nothing for the agent to run against. These are **not** model failures.
>
> So we report **two metrics**:

**Headline (leaderboard methodology, n=100):**
- **Accuracy: 21.0%** (21 PASSED+PASSED_FLAKY out of 100)
- Wilson 95% CI: [14.2%, 30.0%]

**Attempted-only (n=35, the subset where the docker image built and Grok actually ran):**
- **Accuracy: 60.0%** (21 / 35)
- Wilson 95% CI: [43.6%, 74.4%] — wide because n is small

#### Status breakdown

| Status | Count | % of 100 | What it means |
|---|---:|---:|---|
| `AGENT_NO_PATCH (no image built — Grok never ran)` | 61 | 61.0% | No docker image was built — Grok never ran (not a model failure) |
| `PASSED` | 20 | 20.0% | Grok's patch compiled and the must-pass tests passed |
| `AGENT_FAILED_TEST` | 11 | 11.0% | Grok's patch compiled but a must-pass test failed |
| `INFRA_FAILURE_AGENT` | 4 | 4.0% |  |
| `INFRA_FAILURE` | 2 | 2.0% | Verifier infra error |
| `PASSED_FLAKY` | 1 | 1.0% | Passed after a retry of the test execution |
| `AGENT_FAILED_BUILD` | 1 | 1.0% | Grok's patch broke compilation |

#### Wins (Grok 4.3 actually solved these)

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
| `MohamedRejeb__compose-rich-editor-pr_523` | $0.559 | 32 |  |
| `android__nowinandroid-pr_553` | $0.092 | 13 |  |
| `thunderbird__thunderbird-android-pr_7103` | $0.083 | 10 |  |
| `thunderbird__thunderbird-android-pr_7190` | $0.157 | 22 |  |
| `thunderbird__thunderbird-android-pr_8020` | $0.193 | 18 |  |

#### Failures on the attempted subset

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

#### Leaderboard placement

Public scores: mean accuracy over 10 seeds × 100 tasks each ([developer.android.com/bench](https://developer.android.com/bench), snapshot 2026-05-05). Our run: 1 seed × 100 tasks (61 of which never reached the model).

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
| **→** | **Grok 4.3 (this run, 1 seed, leaderboard methodology)** | **21.0%** |

Observation: by the strict leaderboard methodology Grok 4.3 sits well below every officially-tested model. By the attempted-only subset (60.0%, n=35) Grok would slot among mid/upper-tier models — but the small n means a wide CI, so this number is not directly comparable until the build issues are resolved and the rest of the 100 tasks complete.

Single-seed caveat: the public leaderboard averages 10 seeds × 100 tasks. With 1 seed and 35 effective task attempts our CI is much wider than the leaderboard models'.

Full results: [`results/androidbench/full_run_v1/`](results/androidbench/full_run_v1/) — per-instance `*_scores.json`, `patches/`, `trajectories/`, `logs/`, `summary.txt`, `leaderboard_table.md`. Reproduction: [`scripts/`](scripts/) (`00_setup.sh` … `99_master_autopilot.py`).
