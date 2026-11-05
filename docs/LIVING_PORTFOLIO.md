# Living portfolio

The public profile is a view of selected projects, source evidence and curated summaries. It does not establish model accuracy, financial profitability or production readiness.

## Data flow

1. `portfolio/projects.json` controls profile copy, project order and private summaries.
2. The updater reads public repository metadata, the latest stable release and the latest completed workflow on each default branch.
3. A public repository with the `portfolio-profile` topic is also discovered automatically. Forks, archived discovery candidates and this profile repository are excluded. Removing the topic removes an automatically discovered entry after a successful discovery check.
4. `portfolio/snapshot.json` stores normalized public fields. Raw responses, tokens, commit messages, private metadata and release bodies are never stored.
5. The same snapshot produces the managed README block and accessible light/dark SVGs in `assets/portfolio/`. Only the banner and the first three featured public projects receive generated images; the remaining projects use native text.
6. Optional `portfolio/showcases.json` supplies curated public preview captions, three-step walkthroughs and repository-relative source references. Preview PNGs in `assets/showcases/` are maintained separately from generated SVGs. They are not downloaded by the hourly refresh.

A completed workflow is evidence for that named workflow, not a blanket test or quality certification.

## Identity and reading order

The profile uses a dark technical palette with cyan and violet accents. Dark surfaces use `#0D1117` / `#151C29`, text `#F0F6FC`, muted text `#A7B4C5`, cyan `#58D5F7` and violet `#B69BFF`. Light variants use `#F6F8FC` / `#FFFFFF`, text `#152339`, muted text `#526074`, cyan `#096A82` and violet `#6942AF`. The compact banner illustrates data, models and systems. Visual showcases lead with sourced images or an explicitly labeled architecture walkthrough.

Native anchor links and `details` disclosures provide the interaction inside GitHub. Custom JavaScript, filters and hover-controlled widgets are not used. SVG entrance animations run once for at most 4.5 seconds, honor `prefers-reduced-motion`, and retain meaningful static content. Project images never imply live system activity.

The hand-authored README prefix contains the banner reference, introduction and contact links. The banner assets themselves remain generated from the profile fields. The generated block then displays the first three public projects marked `featured`, in registry order. Extra featured projects, ordinary public projects and discovered projects appear in **More Projects**. With no featured projects, the selected-work heading is omitted. A separate **Private Work** disclosure retains published names and summaries, without repository links. **Source Details** holds synchronization, releases and workflow evidence. Empty release headings are omitted.

The hand-authored suffix contains an About disclosure with expertise, research links, working method and background, followed by contact information. Keep factual claims grounded in the published sources; source checks do not establish results or individual research contributions.

Keep each responsive picture inside a paragraph-level HTML block. GitHub otherwise reparses multiline linked pictures and can discard responsive sources or replace the project destination with an image link. Each featured project has a native heading, a preview, short description and an optional expandable walkthrough with its technologies and source references. Mobile walkthroughs stack vertically. PNG previews retain their original appearance in both GitHub themes; generated SVGs adapt to the theme.

Showcase entries must refer to a registered public project. Images use `<project-id>.png`, with optional `<project-id>-mobile.png` for a mobile composition; source paths stay under that project's repository. When a source becomes unavailable, the renderer hides its curated preview, walkthrough and deep links. A temporarily failed check retains cached content with a visible warning, using the existing source policy. New featured projects without curated media receive a generated overview. Private summaries never receive showcase media or source queries.

## Preview provenance

- **Data Science Basics:** the K-means plot reproduces cell 38 of `Data_Science_Basics_Solve.ipynb` at commit `043375eb72da8f4ade97860bd9674c3441ad49cc`. It uses `make_blobs(n_samples=300, centers=4, cluster_std=0.6, random_state=42)` and `KMeans(n_clusters=4, random_state=42, n_init=10)`, plus the same elbow-method fits for k=1…9. Styling and English labels were changed for readability. The mobile composition shows the same clustered points with larger axis labels; the desktop composition also shows the elbow plot. The dataset is synthetic; this is a notebook example, not a performance claim.
- **Bidaya:** authentic setup controls from `frontend/src/pages/Home.jsx`, route `/`, at commit `ded111aa503513051378a9f3e7c7e789516ba4bd`. Captured in the default English/dark frontend at a 1280px desktop viewport with clean browser storage. The screenshot clips the form from target role through AI Coach at natural resolution; CDP capture beyond the viewport avoids the app's sticky-header overlap. Backend and external requests were blocked. No interview responses or backend functionality were simulated. The preview preserves the application's own styling; it is not a redesign or accessibility certification of that application. `image_width` displays this 492px screenshot at a compact 380px desktop width.
- **Saudi Market Intelligence:** the illustration summarizes the data → Python/FastAPI → React architecture in the public README at `7a43abebfd509111ec2f8452c440a79576922be3`. It does not show prices, returns, forecast quality or live data. The existing demo component's incomplete chart rendering was not published as a functioning application screenshot.

Record the source commit, capture route and any fixture data here when replacing a preview. Keep this manual maintenance operation separate from source-metadata refresh; do not run notebooks or application backends in the hourly workflow.

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

Categories: `data`, `ai`, `markets`, `engineering`. IDs must be unique lowercase letters, digits and hyphens. Registering a project manually takes precedence over topic discovery. Set `featured: true` and place the entry among the first three featured public entries to give it a visual card.

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

Only the block between `<!-- portfolio:start -->` and `<!-- portfolio:end -->` is generated. Content outside it remains hand-authored, including the banner picture markup; keep its light/dark sources and 900px mobile breakpoint aligned with `picture()` when editing. Broken or duplicated markers stop generation. Generated SVGs are self-contained, with titles, descriptions, escaped text, no scripts, no external resources and finite entrance motion with a reduced-motion alternative. Images that are no longer generated are removed from `assets/portfolio/`, including cards for projects that are no longer featured. Project entries and evidence remain in the disclosures.

The README uses static SVG images and native links and disclosures. Mobile artwork is selected at viewport widths up to 900px, accounting for GitHub's narrower profile content column on tablets. Test the actual GitHub view at 320, 390, 768 and 1440 pixels in both color schemes. Verify readable text, image loading, keyboard navigation and closed-disclosure height. Source failures remain explicitly labeled on the affected card or text entry.

## Review and rollback

Every registry/renderer change goes through the read-only **Portfolio checks** workflow on the pull request. The refresh workflow tests the updater before writing, validates output afterward and stages only generated paths. Both new workflows pin actions to verified immutable commit SHAs.

Review the generated README, both image themes, the public snapshot and summary changes before merge. Revert the relevant commit to restore a previous renderer or snapshot. If manual repository protections prohibit bot pushes, switch publication to a bot-created PR instead of weakening protection.

The initial automation becomes active after the implementation is merged to `main`. Scheduled workflows in inactive public repositories may be disabled by GitHub; see the linked scheduling documentation.
