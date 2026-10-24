# Contributing

Thanks for helping. Bug reports, docs fixes, and pull requests are all welcome.

## Setup

1. Fork the repository: click **Fork** at the top right of https://github.com/timihack/authwarden.
2. Clone your fork and add the original repository as `upstream`:

```bash
git clone https://github.com/<your-username>/authwarden
cd authwarden
git remote add upstream https://github.com/timihack/authwarden
pip install -e ".[dev]"
pytest
```

## Pull requests

1. Create a branch from the latest `dev`:

```bash
git fetch upstream
git checkout -b my-change upstream/dev
```

2. Make your change, then push the branch to your fork:

```bash
git push -u origin my-change
```

3. Open a pull request from your branch to `timihack/authwarden`. GitHub defaults the target branch to `main`. Change it to **`dev`**. `main` only receives merges from `dev` at release time, and only the maintainer merges.

Before you open the PR:

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
