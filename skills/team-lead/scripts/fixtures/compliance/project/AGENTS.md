# Fixture Project Rules

A scratch project used to demo tiered compliance. The rules below are planted
to cover every tier; nothing here is a real repository.

## Safety

- Never commit `.env` files or anything containing credentials or API keys.
- Never run `git push --force` or `git reset --hard`; history rewrites destroy other people's work.
- Publishing (`npm publish`) and deploys require the user's explicit approval.

## Conventions

- Run `pnpm test` before committing; a red suite never ships.
- Commit messages use Conventional Commits (`feat:`, `fix:`, `chore:`).
- Respect the frobnicate boundary.

## Style

- Prefer small helpers; keep functions under ~40 lines.
- Use single quotes and two-space indent in TypeScript.
