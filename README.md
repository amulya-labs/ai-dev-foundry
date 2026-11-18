# AI Dev Foundry

> ⚠️ **This repo is archived.** It's succeeded by
> [**myorg-starter**](https://github.com/praxiom-systems/myorg-starter), free and
> MIT-licensed, and by [**getmyorg**](https://getmyorg.com), which has the full set of
> agents. Nothing here gets updates anymore, including the Bash policy.

## What replaces it

**[myorg-starter](https://github.com/praxiom-systems/myorg-starter)** is a plugin for
Claude Code and Codex. It ships 10 of the agents below, two skills, and the session
guards: the Bash policy that started here, plus a file-read guard and an edit check.
You install it once for your user, and each host keeps it updated.

```bash
curl -fsSL https://raw.githubusercontent.com/praxiom-systems/myorg-starter/main/bin/install.sh | bash
```

To move a project off the files copied from here:

1. Remove `"Bash(*)"` from `permissions.allow` in `.claude/settings.json`, so Claude
   Code's own prompts ask about commands again. If you'd rather keep them off, run
   `/myorg-starter:config gate on` instead: the guard then asks you about every
   command that isn't routine.
2. Remove the `validate-bash.sh` and `post-bash.sh` hook entries (`.claude/settings.json`,
   `.codex/hooks.json`) and the files they point at.

## Where each agent went

| Foundry agent | Now | In myorg-starter | getmyorg plan |
|---|---|---|---|
| code-reviewer | code-reviewer | ✓ | Free |
| debugger | debugger | ✓ | Free |
| product-owner | product-owner | ✓ | Free |
| security-auditor | security-auditor | ✓ | Free |
| tech-lead | tech-lead | ✓ | Free |
| test-engineer | test-engineer | ✓ | Free |
| senior-dev | fullstack-engineer | ✓ | Free |
| documentation-writer | technical-writer | ✓ | Free |
| systems-architect | systems-architect | ✓ | Focused |
| junior-dev | junior-engineer | ✓ | Scaled |
| ux-designer | product-designer | | Free |
| marketing-lead | product-marketer | | Lean |
| agent-specialist | agent-specialist | | Focused |
| digital-designer | brand-designer | | Focused |
| legal-counsel | legal-counsel | | Focused |
| prod-engineer | sre | | Focused |
| solution-eng | solution-engineer | | Focused |
| claudemd-architect | claudemd-architect | | Scaled |
| data-engineer | data-engineer | | Scaled |
| ml-architect | ml-architect | | Scaled |
| prompt-engineer | prompt-engineer | | Scaled |
| refactoring-expert | refactoring-engineer | | Scaled |
| ui-developer | frontend-engineer | | Scaled |
| pr-refiner | the `/myorg:pr-refine` skill in getmyorg | | |
| career-advisor | retired | | |

The plan is the lowest getmyorg plan that includes the agent.

## What didn't carry over

- **Gemini CLI and opencode:** myorg-starter supports Claude Code and Codex.
- **The Gemini PR-review workflow, NotebookLM sync and `git-subtree-mgr`.**
  The Claude workflows did carry over: `install.sh --workflows` adds `claude.yml`
  and `claude-code-review.yml` to a repo.
- **`manage-ai-configs.sh`:** `install.sh` replaces it.

## License

MIT, see [LICENSE](LICENSE).
