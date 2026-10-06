# Index

## Standard Files
- `PERSONA.md` — who I am (name, vibe, style, evolution log)
- `CREED.md` — what I believe (mission, values, standing orders, boundaries)
- `BOND.md` — who I serve (the owner, currency, pricing region, where the BMad artifacts and my outputs live)
- `MEMORY.md` — what I know (curated long-term knowledge)
- `CAPABILITIES.md` — tools and services the owner has given me (built-in capabilities are listed from the skill on waking)

## Session Logs
- `sessions/` — raw session notes by date (YYYY-MM-DD.md), curated into MEMORY.md when a session winds down

## Project Data (read by my scripts; edit only with the owner's agreement)
- `stories.json` — Jira key -> story number and title, for the token ledger. Optional.
- `savings.json` — project-specific suggested savings laid over the ledger's defaults. Optional.
- `claude-prices.json` — Claude API list prices per model as last read from Anthropic's pricing page, with the date. Written by `claude_prices.py --save` at close-out.
- `rate-card.json` — the employee rate card: currency, source, date, and a day rate per role. Owned by capability RT; created the first time a rate is given.

## My Files
_This section grows as I create organic files. Update it when adding new files._
- `estimates.md` — one line per cost estimate built: name, date, grand total, the model's path, and what the owner decided about it. Created with the first estimate.
