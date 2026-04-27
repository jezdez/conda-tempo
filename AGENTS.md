# Rules for AI coding agents

This file collects recurring conventions for agents working in the
conda-tempo research repo and on the PR branches it drives across
`conda/conda`, `conda/conda-libmamba-solver`,
`conda/conda-package-handling`, and `conda/conda-package-streaming`.

## Single source of truth for the executive summary

The Track B research report
(`track-b-transaction.md#executive-summary`) holds the canonical,
measurement-linked executive summary. It is kept in sync with the
Changelog and Phase-4 numbers and carries its own "Last refreshed"
date. When updating the GitHub tracking epic (currently
conda/conda#15969), **do not duplicate** the full summary in the issue
body — link to the anchor in the research report instead. Duplicated
summaries drift and the one in the ticket was stale within a day last
time.

## PR / issue conventions

- **Titles**: sentence case, no `perf:` or other conventional-commit
  prefix, symbols in backticks, end with `(#<PR-number>)` or the
  short ID pattern (`(B1)`, `(A2/A3)`) that Track A uses. Mirror
  Track A (e.g. [conda/conda#15868](https://github.com/conda/conda/pull/15868)
  "Lazy subcommand parser loading (A2/A3)").
- **Bodies**: fill in the repo's `PULL_REQUEST_TEMPLATE.md`
  (Description, Checklist). Keep technical depth in the PR body;
  the news entry stays user-facing.
- **News entries**: one short paragraph, user-facing phrasing, no
  internal `BXX`/`SXX` identifiers (those belong to the research
  report only). Name the file `<PR-number>-<slug>` per repo convention.
- **Commit messages**: imperative present tense, end subject with
  the PR number in `(#NNNN)` format. Do **not** include `BXX`/`SXX`
  identifiers in commit subjects or bodies — those are easier for
  reviewers to link to when referenced by PR number.
- **Code comments**: reference PRs (`See #15970`) and the tracking
  epic (`conda/conda#15969`), not `BXX`/`SXX`. Those names are valid
  inside the research report only; GitHub does not auto-link them.

## Pre-commit before every push

Each sibling repo runs `pre-commit` hooks in CI. Always
`pre-commit run --all-files` (or scoped to touched files) on a branch
before `git push`, and **before opening or re-pushing a PR**. Fix
every failure (trailing whitespace, imports, type stubs, newline at
EOF, etc.) and re-commit; do **not** push failures and rely on CI to
surface them.

`pre-commit install` once per sibling-repo checkout so the hooks
run on every `git commit` locally too.

## Force-pushes on drafts

Draft PRs may be force-pushed freely. Use `--force-with-lease`, not
`--force`, to avoid clobbering reviewer-pushed changes. Once a PR is
out of draft and has review, switch to stacking fixups instead of
rewrites.

## Research report is the primary artefact

Numbers, measurement methodology, per-suspect deep-dives, and the
changelog of research decisions live in
`track-b-transaction.md` (Track B) or `track-a-startup.md` (Track A).
PR bodies and news entries summarise; the report has the detail and
raw data under `data/phase1/`, `data/phase2/`, `data/phase4/`.
Always refresh the report's **Executive Summary → Last refreshed**
date when numbers change.

## Secrets and hooks

Do not bypass commit hooks (`--no-verify`, `--no-gpg-sign`). GPG
signing is configured via `commit.gpgsign = true`; `git filter-branch`
does not re-sign automatically — follow the rewrite with a
`--commit-filter 'git commit-tree -S "$@"'` pass when rewriting
history.
