# Living portfolio

The public profile is a view of selected projects, source evidence and curated summaries. It does not establish model accuracy, financial profitability or production readiness.

## Data flow

1. `portfolio/projects.json` controls profile copy, project order and private summaries.
2. The updater reads public repository metadata, the latest stable release and the latest completed workflow on each default branch.
3. A public repository with the `portfolio-profile` topic is also discovered automatically. Forks, archived discovery candidates and this profile repository are excluded. Removing the topic removes an automatically discovered entry after a successful discovery check.
4. `portfolio/snapshot.json` stores normalized public fields. Raw responses, tokens, commit messages, private metadata and release bodies are never stored.
5. The same snapshot produces the managed README block and accessible light/dark SVGs in `assets/portfolio/`.

The map groups portfolio themes and shared skills. It does not assert technical dependencies between projects. A completed workflow is evidence for that named workflow, not a blanket test or quality certification.

## Add a public project

For automatic inclusion, add the **`portfolio-profile`** repository topic on GitHub. The repository must be public and owned by `Abdulrahman-S-Asiri`. GitHub's description supplies its initial summary, and it appears in the engineering category.

For controlled copy, category, stack and order, add an entry to `portfolio/projects.json`:

```json
{
  "id": "example-project",
  "name": "Example project",
  "visibility": "public",
  "repository": "example-project",
  "category": "data",
  "summary": "A specific, factual description of its scope.",
  "stack": ["Python"],
  "featured": false
}
```

Categories: `data`, `ai`, `markets`, `engineering`. IDs must be unique lowercase letters, digits and hyphens. Registering a project manually takes precedence over topic discovery.

## Private summaries

Use `visibility: "summary-only"`, omit `repository`, and supply a `publication_basis` documenting approval or previously public scope. The updater does not make API requests for these entries. This release reuses summaries already present in the public README at commit `5c0dddf46666c4f00a4a45c5836f08a2e8dc79ec`, including Stock Agents, CodeForge and Stock_101.

Changes to private summaries are reviewed in the same PR as the registry change. Never add client information, credentials, financial records or private operating metrics to this public registry.

## Refresh behavior

- The updater runs hourly, at minute 17 UTC, and after changes to portfolio configuration, renderer or README on `main`.
- It can also be started manually with the **Refresh portfolio** workflow.
- GitHub schedule timing can be delayed. Dates shown on cards are the actual successful check date in UTC, not a promise of real-time delivery.
- A successful public metadata check produces `current`. A temporary failure preserves the last verified public snapshot as `stale` and visibly labels it cached.
- A missing, private, deleted, redirected or inaccessible source returning 403/404/410 or a redirect loses cached remote metadata and links. A 403 specifically identified as rate limiting retains cached public data. Its curated description remains in the registry; remove the entry to withdraw that description too.
- Release and workflow checks are independent. Missing releases and missing workflows are ordinary empty states; failed checks are explicitly marked in the evidence table.
- Discovery failures retain previous discovered candidates with a visible stale discovery status. Each candidate's public visibility is still checked individually.
- Commits happen only when public fields, source status or the UTC check date changes. Unchanged hourly runs do not create commits.
- The default-branch guard and concurrency group prevent feature-branch publication and overlapping refresh writes. A push race fails visibly rather than force-pushing history.

## Optional immediate refresh from another repository

The hourly path works without adding credentials to source repositories. For faster updates after a meaningful release, send `repository_dispatch` with event type `portfolio-update` to this profile repository. The updater treats the signal only as a refresh request; it ignores the payload and reads sources itself.

```text
POST /repos/Abdulrahman-S-Asiri/Abdulrahman-S-Asiri/dispatches
{"event_type":"portfolio-update"}
```

Configure a narrowly scoped GitHub App or fine-grained token with **Contents: write** on this destination repository only, stored as a GitHub Actions secret. The source repository's built-in `GITHUB_TOKEN` is scoped to that source and cannot write this destination. No cross-repository credentials are created or required for the baseline implementation.

References: [GitHub events and scheduling](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows), [repository discovery and dispatch API](https://docs.github.com/en/rest/repos/repos), [GITHUB_TOKEN scope](https://docs.github.com/en/actions/concepts/security/github_token).

## Local checks

Python 3.13+ is sufficient. There are no third-party runtime dependencies.

```text
python -m unittest discover -s tests -v
python tools/update_portfolio.py --check
python tools/update_portfolio.py
python tools/update_portfolio.py --refresh
```

The first three commands work offline. Refresh uses GitHub's public API; `GITHUB_TOKEN` is optional locally and supplied automatically in the refresh workflow. A token never appears in logged output or checked-in data. API calls refuse redirects, cap response size, use timeouts and retry temporary network/server failures once.

Only the block between `<!-- portfolio:start -->` and `<!-- portfolio:end -->` is generated. Content outside it remains hand-authored. Broken or duplicated markers stop generation. Generated SVGs are self-contained, with titles, descriptions, escaped text, no scripts, no external resources and no motion.

The visual system uses cyan/violet gradients, restrained light effects, category icons and theme-specific contrast. Decorative nodes and bars represent portfolio themes, not financial results or technical dependencies. Interactive hover elevation belongs to the standalone HTML preview; the GitHub README displays the static SVG treatment.

## Review and rollback

Every registry/renderer change goes through the read-only **Portfolio checks** workflow on the pull request. The refresh workflow tests the updater before writing, validates output afterward and stages only generated paths. Both new workflows pin actions to verified immutable commit SHAs.

Review the generated README, both image themes, the public snapshot and summary changes before merge. Revert the relevant commit to restore a previous renderer or snapshot. If manual repository protections prohibit bot pushes, switch publication to a bot-created PR instead of weakening protection.

The initial automation becomes active after the implementation is merged to `main`. Scheduled workflows in inactive public repositories may be disabled by GitHub; see the linked scheduling documentation.
