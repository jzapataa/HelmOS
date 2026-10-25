---
name: analyze-social-sample
description: Analyze a small publication-metrics CSV with the HelmOS local Core and give a cautious, reviewed interpretation of the recorded counts. Use for bounded sample analyses, not full HelmOS Social strategy.
---

# Analyze a social sample

Use a CSV with columns `post_id,platform,impressions,likes,comments,shares`. The local Core accepts 1–20 rows for Instagram, TikTok and LinkedIn, non-negative integer counts, and unique publication IDs. The bundled `examples/social_sample.csv` is synthetic; never describe its figures as real account performance.

From the repository root, run `python -m helmos run social.sample --csv PATH` and save the returned execution ID. Use `python -m helmos show ID` to retrieve the persisted result. The Core validates the CSV and calculates sums for the supplied counts and `interactions = likes + comments + shares` per publication. This interaction sum is only a calculation for this sample, not an approved HelmOS Social KPI. It does not invoke Codex agents.

Interpret only the `data`, `results` and `warnings` actually returned. State the sample size and that no period or platform-specific definitions are supplied. Do not infer trends, causality, engagement rates, audience growth, or an editorial strategy from this small sample. Do not write complete posts. If the record failed, report the error instead of supplying missing values.

When Codex subagents are available and the user asks for delegation, give one agent the interpretation and a second agent an independent check of claims against the same Core record. Identify their actual roles and report the review outcome separately from the Core's deterministic calculations. If no subagents ran, do not claim they did.
