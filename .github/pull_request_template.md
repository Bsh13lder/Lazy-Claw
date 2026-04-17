<!-- Thanks for contributing to LazyClaw! -->

## Summary

<!-- One or two sentences. What does this PR change and why? -->

## Type

- [ ] Bug fix (non-breaking)
- [ ] New feature (non-breaking)
- [ ] Breaking change (would require a migration note in CHANGELOG)
- [ ] Docs / chore / refactor (no behavior change)

## Test plan

- [ ] `pytest tests/` still green
- [ ] `cd web && npx tsc -b` still green
- [ ] Manually tested the change (describe below)

<!-- How did you verify this works end-to-end? -->

## Checklist

- [ ] Does NOT weaken E2E encryption guarantees (no plaintext user content added to logs / DB / network)
- [ ] No secrets, API keys, or personal data committed
- [ ] CHANGELOG.md updated if this is a user-visible change
- [ ] README.md updated if this changes how users install or invoke the agent
- [ ] Commit messages follow the conventional style (`feat:` / `fix:` / `docs:` / `refactor:` / `test:`)
