[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$CommitMessage,

    [switch]$Yes
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

$repoRoot = (& git rev-parse --show-toplevel).Trim()
if (-not $repoRoot) {
    throw "This command must run inside a Git repository."
}

Set-Location $repoRoot

$status = (& git status --porcelain)
if (-not $status) {
    Write-Host "No changes to publish."
    exit 0
}

Write-Host "Changes to publish:"
& git status --short

if (-not $Yes) {
    $answer = Read-Host "Stage all changed files, commit, and push? Type YES to continue"
    if ($answer -ne "YES") {
        Write-Host "Canceled."
        exit 1
    }
}

$branch = (& git branch --show-current).Trim()
if (-not $branch) {
    throw "Could not determine the current branch."
}

& git add -A
& git commit -m $CommitMessage
& git push -u origin $branch

Write-Host "Published branch '$branch' with commit message: $CommitMessage"
