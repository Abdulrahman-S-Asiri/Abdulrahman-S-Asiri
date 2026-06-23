# Public Repo Launch Playbook

This playbook turns a new public repository into a project that is easy to push, review, and publish.

## What Can Be Automated

GitHub cannot publish local changes that have not been committed and pushed. The reliable automation pattern is:

1. A local script creates or updates the repo and pushes changes.
2. GitHub Actions runs on `main`.
3. GitHub Pages publishes the latest site artifact automatically.

For full organization-wide workflow templates, GitHub expects a separate repository named `.github` with a `workflow-templates` directory. This profile repo includes the templates and scripts, but the global template behavior requires copying them into that special `.github` repo.

## Prerequisites

Install and authenticate GitHub CLI:

```powershell
gh auth login
gh auth status
```

Make sure Git is configured:

```powershell
git config --global user.name "Abdulrahman Saad Asiri"
git config --global user.email "your-github-email@example.com"
```

## Create A New Public Static Site Repo

From the directory where you keep projects:

```powershell
.\Abdulrahman-S-Asiri\tools\new-public-repo.ps1 `
  -Name my-public-site `
  -Description "Public portfolio project"
```

The script will:

- create a public GitHub repository
- clone it locally
- add a clean `README.md`
- add a starter `index.html`
- add a GitHub Pages workflow
- commit and push the first version
- attempt to enable Pages with workflow deployment

After that, every push to `main` publishes through GitHub Actions.

## Publish Updates From Any Repo

From inside an existing repo:

```powershell
..\Abdulrahman-S-Asiri\tools\publish-current-repo.ps1 `
  -CommitMessage "Improve project landing page"
```

The script shows the changed files, asks for confirmation, commits, and pushes the current branch. Add `-Yes` only when you are sure every changed file belongs in the commit.

## Add Pages Publishing To An Existing Static Repo

Copy the workflow:

```powershell
New-Item -ItemType Directory -Force .github\workflows
Copy-Item ..\Abdulrahman-S-Asiri\templates\github-actions\pages-static.yml .github\workflows\pages.yml
```

Then commit and push:

```powershell
git add .github\workflows\pages.yml
git commit -m "Add GitHub Pages deployment"
git push
```

Enable GitHub Pages workflow deployment:

```powershell
gh api -X POST "repos/OWNER/REPO/pages" -f build_type=workflow
```

If the Pages site already exists, use:

```powershell
gh api -X PUT "repos/OWNER/REPO/pages" -f build_type=workflow
```

## Optional: Make Workflow Templates Available Globally

Create a repo named `.github` under the account or organization where you want templates to appear. Then copy:

```text
.github/workflow-templates/static-pages.yml
.github/workflow-templates/static-pages.properties.json
.github/workflow-templates/static-pages.svg
```

GitHub will show that workflow template when creating workflows in matching repositories.

## Public Project Quality Checklist

- Clear README with problem, demo, setup, architecture, tests, and deployment.
- CI runs on pull requests and pushes.
- Pages or production deployment is automatic after merge to `main`.
- Secrets are never committed; provide `.env.example` instead.
- Screenshots or demo links are current.
- Issues or roadmap explain what is planned next.
- License is explicit when reuse is allowed.
- Every claim in the README is supported by code, data, screenshots, or docs.
