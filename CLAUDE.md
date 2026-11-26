# Project rules for Claude Code

Product: local-first African market intelligence platform. Owner: Mojisola (non-developer; explain decisions in plain language).

## Money rule
Build first. Validate second. Spend money only when the business gives a reason.
Before adding any tool, ask: can we do this for free right now? If yes, use the free option. If no, build it as a switched-off plug-in and say so.

## Data rules
- Never invent statistics, sources, metrics or findings. Missing data is shown as missing.
- Never collect private data or bypass logins, robots.txt, platform rules or rate limits.
- Every stored number keeps its source_id and timestamps. Snapshots are append-only.
- Sample data must be is_sample=1 and labelled SAMPLE.
- Do not assume an API exists or behaves a certain way without checking its official documentation.

## Code rules
- Never hardcode keys. Secrets live in .env (placeholders in .env.example).
- New data source = new file in intel/providers/ + registry entry + fixture test.
- Run `python -m pytest -q` before saying something works. Fix failures before moving on.
- Never delete working features without the owner's permission.
- Keep README.md and docs/ updated when behaviour changes.
- Keep pages responsive (mobile, tablet, desktop) and the visual style consistent with static/style.css.
