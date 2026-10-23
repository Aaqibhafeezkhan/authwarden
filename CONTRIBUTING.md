# Contributing

Thanks for helping. Bug reports, docs fixes, and pull requests are all welcome.

## Setup

```bash
git clone https://github.com/timihack/authwarden
cd authwarden
pip install -e ".[dev]"
pytest
```

## Pull requests

- Branch from `dev` and open the PR against `dev`. `main` only receives merges from `dev` at release time.
- Add or update tests for any behavior change. `pytest` must pass.
- CI also runs `bandit` and `pip-audit`. To run them locally: `pip install bandit pip-audit`, then `bandit -r authwarden -ll` and `pip-audit .`
- Match the style of the file you are editing.
- Add a line under `[Unreleased]` in `CHANGELOG.md`.

## Docs

Docs live in `docs/` and are built with MkDocs Material.

```bash
pip install mkdocs-material
mkdocs serve
```

`mkdocs build --strict` must pass.

## Issues

Use the issue templates. For a bug, a minimal reproduction is the most useful thing you can include.

Do not open a public issue for a security vulnerability. Follow [SECURITY.md](SECURITY.md) instead.

## Before a large change

Check the [roadmap discussion](https://github.com/timihack/authwarden/discussions) first. Planned work such as ORM stores, cookie transport, and SAML is tracked there, and a quick comment saves you from building something that is already in progress.