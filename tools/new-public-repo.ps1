[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9_.-]+$')]
    [string]$Name,

    [string]$Description = "",

    [string]$Owner = "",

    [string]$Directory = (Get-Location).Path,

    [switch]$Private,

    [switch]$SkipPages
)

$ErrorActionPreference = "Stop"

function Require-Command {
    param([Parameter(Mandatory = $true)][string]$CommandName)

    if (-not (Get-Command $CommandName -ErrorAction SilentlyContinue)) {
        throw "Required command '$CommandName' was not found on PATH."
    }
}

Require-Command git
Require-Command gh

& gh auth status | Out-Null

$repoName = if ($Owner.Trim()) { "$Owner/$Name" } else { $Name }
$target = Join-Path -Path $Directory -ChildPath $Name

if (Test-Path -LiteralPath $target) {
    throw "Target directory already exists: $target"
}

$createArgs = @("repo", "create", $repoName)
$createArgs += if ($Private) { "--private" } else { "--public" }
if ($Description.Trim()) {
    $createArgs += @("--description", $Description)
}
$createArgs += "--clone"

Write-Host "Creating repository $repoName..."
Push-Location $Directory
try {
    & gh @createArgs
}
finally {
    Pop-Location
}

$previousLocation = Get-Location
Set-Location $target
try {
    New-Item -ItemType Directory -Force -Path ".github\workflows" | Out-Null
    $fullName = (& gh repo view --json nameWithOwner --jq ".nameWithOwner").Trim()

    @"
# $Name

Describe the project in one paragraph: who it serves, what problem it solves, and what makes the implementation credible.

## Demo

GitHub Pages will publish this repository after the first successful workflow run.

## Development

~~~powershell
# Edit files, then publish:
git status
git add .
git commit -m "Describe the change"
git push
~~~

## Quality Bar

- Clear problem statement
- Reproducible setup
- Automated deployment
- Screenshots or demo link
- Known limitations
"@ | Set-Content -LiteralPath "README.md" -Encoding UTF8

    @"
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>$Name</title>
    <style>
      :root {
        color-scheme: light dark;
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        line-height: 1.5;
      }

      body {
        margin: 0;
        min-height: 100vh;
        display: grid;
        place-items: center;
        background: #0f172a;
        color: #f8fafc;
      }

      main {
        width: min(920px, calc(100% - 40px));
        padding: 72px 0;
      }

      h1 {
        margin: 0 0 16px;
        font-size: clamp(2.5rem, 7vw, 5.5rem);
        line-height: 0.95;
        letter-spacing: 0;
      }

      p {
        max-width: 720px;
        color: #cbd5e1;
        font-size: 1.125rem;
      }

      a {
        color: #38bdf8;
      }
    </style>
  </head>
  <body>
    <main>
      <p>Public project</p>
      <h1>$Name</h1>
      <p>Replace this starter page with the actual product, demo, or documentation site. Keep the first screen specific, credible, and easy to evaluate.</p>
      <p><a href="https://github.com/$fullName">View source on GitHub</a></p>
    </main>
  </body>
</html>
"@ | Set-Content -LiteralPath "index.html" -Encoding UTF8

    @'
name: Publish static site

on:
  push:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read
  pages: write
  id-token: write

concurrency:
  group: pages
  cancel-in-progress: false

jobs:
  deploy:
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v7

      - name: Configure Pages
        uses: actions/configure-pages@v6

      - name: Upload static site
        uses: actions/upload-pages-artifact@v5
        with:
          path: .

      - name: Deploy to GitHub Pages
        id: deployment
        uses: actions/deploy-pages@v5
'@ | Set-Content -LiteralPath ".github\workflows\pages.yml" -Encoding UTF8

    @"
.DS_Store
Thumbs.db
.env
.env.*
!.env.example
node_modules/
dist/
build/
.venv/
__pycache__/
"@ | Set-Content -LiteralPath ".gitignore" -Encoding UTF8

    & git branch -M main

    if (-not $SkipPages) {
        try {
            & gh api -X POST "repos/$fullName/pages" -f build_type=workflow | Out-Null
        }
        catch {
            Write-Warning "Could not enable Pages before first push. The script will try again after pushing."
        }
    }

    & git add README.md index.html .gitignore .github/workflows/pages.yml
    & git commit -m "Initialize public project"
    & git push -u origin main

    if (-not $SkipPages) {
        $fullName = (& gh repo view --json nameWithOwner --jq ".nameWithOwner").Trim()
        try {
            & gh api -X POST "repos/$fullName/pages" -f build_type=workflow | Out-Null
        }
        catch {
            try {
                & gh api -X PUT "repos/$fullName/pages" -f build_type=workflow | Out-Null
            }
            catch {
                Write-Warning "Could not enable Pages automatically. In GitHub, open Settings > Pages and choose GitHub Actions."
            }
        }
    }

    Write-Host "Created, committed, pushed, and prepared Pages for $repoName."
}
finally {
    Set-Location $previousLocation
}
