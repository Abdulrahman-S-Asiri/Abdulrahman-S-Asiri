# Maintaining the living portfolio

The profile opens with Abdulrahman's introduction and a figure from the ServeDoctorBench A100 experiment. Edit this research material directly in the README and keep its figures consistent with the case study and team poster. These links document the experimental setup, measurements and team contributions. [Case study](research/servedoctorbench-case-study.md) · [Poster](research/Tasaru_A0_ServeDoctorBench.pdf)

## Where to make a change

| Change | Source |
| --- | --- |
| Masthead, research, education, experience or contact details | Edit the README outside `<!-- portfolio:start -->` and `<!-- portfolio:end -->`. The [masthead and A100 figure files](../assets/blue-ink/) have separate light, dark and mobile versions. |
| Project names, summaries, technology, order or featured status | Edit the ordered entries in [projects.json](../portfolio/projects.json). |
| Project evidence or a Bidaya screenshot | Edit [showcases.json](../portfolio/showcases.json) and the corresponding file in [assets/showcases](../assets/showcases/). Use a repository-relative source path pinned to the full commit that supports the claim. |
| Public check date, release or workflow result | Refresh [the public snapshot](../portfolio/snapshot.json) through the updater. |
| Generated project illustrations or README layout | Edit [the renderer](../tools/update_portfolio.py) and regenerate. It removes obsolete SVGs from its own `assets/portfolio/` directory. |

The generated block presents featured public projects in registry order. Saudi Market Intelligence and Bidaya currently have illustrated features. Bidaya also has a disclosure with a screenshot from its interface. The **More projects** index starts with Data Science Basics, followed by the remaining public projects and eligible discoveries. Private summaries and public source records have separate disclosures.

## Add a public project

For automatic inclusion, add the **`portfolio-profile`** topic to a public repository owned by `Abdulrahman-S-Asiri`. The updater includes its GitHub description in the index. It excludes forks, archived discovery candidates and the profile repository. After a successful discovery check, removing the topic removes the automatic entry.

To control a project's copy, category or placement, register it explicitly. This takes precedence over topic discovery.

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

Choose `data`, `ai`, `markets` or `engineering` for the category. IDs use lowercase letters, digits and hyphens and must be unique. Set `featured` to `true` for a full project entry or `false` for an index entry. A featured entry can use `subtitle` for a brief description beneath its name. Curated implementation references use the form `blob/<40-character-commit-SHA>/<path>`. Match each reference to the file that backs its label.

## Private summaries

Set `visibility` to `summary-only`, omit `repository`, and record the approval or previously public scope in `publication_basis`. Review the public wording with the registry change. The updater queries only public project entries. The snapshot and source disclosure contain only public checks. The current Stock Agents, CodeForge and Stock_101 summaries are based on descriptions already published in the profile. [Earlier public README](https://github.com/Abdulrahman-S-Asiri/Abdulrahman-S-Asiri/blob/5c0dddf46666c4f00a4a45c5836f08a2e8dc79ec/README.md)

## Read the source and release records

The hourly refresh requests repository metadata, the latest stable release and the latest completed workflow on the default branch. Each check date is in UTC. The snapshot stores selected public fields and source links. The run link identifies the specific workflow, conclusion and date.

| Status | What readers see |
| --- | --- |
| Current | The last successful check and verified repository links. Registered archived projects retain an archive label. |
| Cached | A temporary failure or rate limit preserves the last verified public record and its original date. |
| Unavailable or unchecked | The entry retains its curated summary. The updater suppresses repository links, showcase evidence and feature illustrations. Loss of access or a redirect clears cached remote fields. |
| No stable release or completed workflow | The source responded without a matching release or run. A failed optional check has its own verification message. |

If discovery fails, the updater retains previous candidates, marks the discovery check stale and rechecks each candidate's visibility. The `Refresh portfolio` workflow runs at minute 17 of each UTC hour, on the configured pushes to `main`, or on manual dispatch. Scheduled runs can be delayed. Unchanged output produces no commit. [Refresh workflow](../.github/workflows/update-portfolio.yml)

## Request an immediate refresh

Run **Refresh portfolio** from the profile's Actions tab. A source repository can also send a `repository_dispatch` event to request a refresh after a release. The updater fetches the evidence from GitHub and ignores the event payload.

```text
POST /repos/Abdulrahman-S-Asiri/Abdulrahman-S-Asiri/dispatches
{"event_type":"portfolio-update"}
```

For cross-repository dispatch, use a GitHub App or fine-grained token scoped to the destination with **Contents: write**, stored as an Actions secret. The hourly workflow needs no credential in the source repository. [Dispatch endpoint](https://docs.github.com/en/rest/repos/repos#create-a-repository-dispatch-event) · [GitHub token scope](https://docs.github.com/en/actions/concepts/security/github_token)

## Reproduce and review a change

From the repository root with Python 3.13 or later, run the following offline checks. The render command writes the managed files. The final command verifies that the checked-in README block, snapshot and generated SVGs match the inputs.

```text
python -m unittest discover -s tests -v
python tools/update_portfolio.py
python tools/update_portfolio.py --check
```

To request new public metadata, run `python tools/update_portfolio.py --refresh`. A local `GITHUB_TOKEN` is optional. GitHub Actions supplies its own token during the scheduled refresh. The updater limits response sizes, refuses redirects and suppresses remote payloads in its logs.

Review the complete README at desktop and phone widths in both GitHub themes. Check the native text, image alternatives, Bidaya screenshot, pinned links and closed disclosures. Verify that regenerated output preserved all hand-authored content outside the two markers. Broken or duplicated markers stop generation.

Pull requests run the read-only **Portfolio checks** workflow. The refresh job tests before publication, verifies the render afterward and stages only generated paths. Its default-branch guard and concurrency group limit automatic writes. Revert the relevant commit to restore a previous version. [Portfolio checks](../.github/workflows/portfolio-ci.yml)
