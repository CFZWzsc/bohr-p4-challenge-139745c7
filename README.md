# Runtime contract

Bohrium Playground Period 4 submission — challenge `challenge-139745c7` (track: `research-library`).

This repository holds the complete skill package for the challenge: the skill
definition, its executable pipeline and all supporting documents. No precomputed
answers, private data or credentials are included.

## Skill

- Name: `research-file-retrieval-locator`
- Summary: Use when a research-library case asks which passage, section, page or version of local files answers a question under hard filters, and needs citation-grade back-links; runs a stdlib-only deterministic indexer over /input and writes a contract-complete /app/output/result.json.

## Contents

| File |
|------|
| `SKILL.md` |
| `assets/policy.json` |
| `examples/demo-case/01_project/2021-buffer-notes.md` |
| `examples/demo-case/01_project/2022-revision.md` |
| `examples/demo-case/02_protocols/protocol-A.txt` |
| `examples/demo-case/03_data/measurements.csv` |
| `examples/demo-case/04_paper/manuscript.html` |
| `examples/demo-case/case.json` |
| `examples/quickstart.md` |
| `references/evidence-locators.md` |
| `references/failure-modes.md` |
| `references/retrieval-protocol.md` |
| `scripts/run_skill.py` |
| `scripts/sk_entry.py` |
| `README.md` |

## How to run

```bash
python scripts/sk_entry.py --input /input --work /app/work --out /app/output
```

Two passes are intended: the first writes `/app/work/agent_tasks.json` (evidence
skeleton) and `/app/output/result.json`; after the agent writes
`/app/work/agent_judgement.json`, the second pass merges and validates the result.
See `SKILL.md` and `references/two-phase-workflow.md`.

## Notes

- Pure Python standard library; no network access required.
- Every claim is anchored to paragraph-level evidence; unknown items are reported
  in `uncertainty` instead of being fabricated.
