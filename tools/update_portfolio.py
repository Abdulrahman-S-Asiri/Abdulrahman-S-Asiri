#!/usr/bin/env python3
"""Refresh public GitHub metadata and render a reproducible profile. Stdlib only."""

import argparse
import copy
from datetime import date, datetime, timezone
import html
import json
import os
from pathlib import Path
import re
import sys
import textwrap
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import xml.etree.ElementTree as ET

START, END = "<!-- portfolio:start -->", "<!-- portfolio:end -->"
CATEGORIES = {"data": "Data & learning", "ai": "AI & language", "markets": "Markets & decisions", "engineering": "Engineering & experiences"}
COLORS = {"data": "#38bdf8", "ai": "#a78bfa", "markets": "#34d399", "engineering": "#fbbf24"}
SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,79}\Z")
REPO = re.compile(r"[A-Za-z0-9_.-]{1,100}\Z")
CONCLUSIONS = {"success", "failure", "cancelled", "timed_out", "neutral", "skipped", "action_required", "stale", "startup_failure"}


def plain(value, limit=240):
    if not isinstance(value, str):
        raise ValueError("Expected text")
    return " ".join(value.split())[:limit]


def safe_url(value, prefix=None):
    if not isinstance(value, str):
        raise ValueError("Expected a URL")
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.netloc or parts.username or parts.password or any(ord(c) < 32 for c in value):
        raise ValueError("Only public HTTPS links are allowed")
    if prefix and (not value.startswith(prefix) or len(value) > 1000):
        raise ValueError("Unexpected GitHub source URL")
    return value


def iso_date(value):
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Source timestamp must include timezone")
    return parsed.astimezone(timezone.utc).date().isoformat()


def validate_registry(data):
    if set(data) != {"version", "owner", "discovery_topic", "profile", "projects"} or data["version"] != 1:
        raise ValueError("Unsupported registry structure")
    if not REPO.fullmatch(data["owner"]) or not SLUG.fullmatch(data["discovery_topic"]):
        raise ValueError("Invalid owner or discovery topic")
    if set(data["profile"]) != {"name", "role", "direction", "location"}:
        raise ValueError("Invalid profile fields")
    for text in data["profile"].values():
        if not plain(text, 90) or text != plain(text, 90):
            raise ValueError("Profile text must be short and single-line")
    seen_ids, seen_repos = set(), set()
    allowed = {"id", "name", "visibility", "repository", "category", "summary", "stack", "featured", "publication_basis"}
    for project in data["projects"]:
        if set(project) - allowed or not {"id", "name", "visibility", "category", "summary", "stack", "featured"} <= set(project):
            raise ValueError("Invalid project fields")
        pid = project["id"]
        if not SLUG.fullmatch(pid) or pid in seen_ids:
            raise ValueError("Project IDs must be unique safe filenames")
        seen_ids.add(pid)
        if project["category"] not in CATEGORIES or type(project["featured"]) is not bool:
            raise ValueError("Invalid category or featured flag")
        for key, limit in (("name", 55), ("summary", 240)):
            if not plain(project[key], limit) or project[key] != plain(project[key], limit):
                raise ValueError("Project text exceeds its display limit")
        if not isinstance(project["stack"], list) or len(project["stack"]) > 6 or any(not isinstance(t, str) or t != plain(t, 24) for t in project["stack"]):
            raise ValueError("Invalid stack labels")
        if project["visibility"] == "public":
            repo = project.get("repository", "")
            if not REPO.fullmatch(repo) or repo.lower() in seen_repos or "publication_basis" in project:
                raise ValueError("Invalid or duplicate public repository")
            seen_repos.add(repo.lower())
        elif project["visibility"] == "summary-only":
            if "repository" in project or not plain(project.get("publication_basis", "")):
                raise ValueError("Private summaries require a publication basis and cannot request repository data")
        else:
            raise ValueError("Invalid project visibility")
    return data


class SourceError(Exception):
    def __init__(self, status=None):
        self.status = status


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHub:
    def __init__(self):
        self.token = os.environ.get("GITHUB_TOKEN", "")
        self.opener = build_opener(NoRedirects())

    def get(self, path):
        if not path.startswith(("/repos/", "/users/")) or ".." in path or "#" in path:
            raise ValueError("Unexpected API path")
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10", "User-Agent": "living-portfolio"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        request = Request("https://api.github.com" + path, headers=headers)
        for attempt in range(2):
            try:
                with self.opener.open(request, timeout=15) as response:
                    raw = response.read(2_000_001)
                    if len(raw) > 2_000_000:
                        raise SourceError()
                    return json.loads(raw)
            except HTTPError as exc:
                if attempt == 0 and (exc.code == 429 or exc.code >= 500):
                    time.sleep(2)
                    continue
                status = 429 if exc.code == 403 and exc.headers.get("X-RateLimit-Remaining") == "0" else exc.code
                raise SourceError(status) from None
            except (URLError, TimeoutError, OSError, ValueError):
                if attempt == 0:
                    time.sleep(1)
                    continue
                raise SourceError() from None


def discover(registry, api):
    projects = []
    registered = {p.get("repository", "").lower() for p in registry["projects"]}
    ids = {p["id"] for p in registry["projects"]}
    for page in range(1, 21):
        batch = api.get(f'/users/{registry["owner"]}/repos?per_page=100&page={page}&type=owner')
        if not isinstance(batch, list):
            raise SourceError()
        for repo in batch:
            if not isinstance(repo, dict) or not isinstance(repo.get("topics", []), list):
                raise SourceError()
            name = repo.get("name", "")
            if repo.get("private") is not False or repo.get("fork") or repo.get("archived") or registry["discovery_topic"] not in repo.get("topics", []):
                continue
            if not REPO.fullmatch(name) or name.lower() in registered or name.lower() == registry["owner"].lower():
                continue
            pid = "auto-" + re.sub(r"[^a-z0-9-]", "-", name.lower())[:65]
            if pid in ids:
                raise ValueError("Discovered project ID collision; register it manually")
            ids.add(pid)
            projects.append({"id": pid, "name": plain(name, 55), "visibility": "public", "repository": name, "category": "engineering", "summary": plain(repo.get("description") or "Public project. Open the repository for scope, setup and evidence."), "stack": [], "featured": False})
        if len(batch) < 100:
            return sorted(projects, key=lambda p: p["repository"].lower())
    raise SourceError()


def fetch_project(registry, project, api, today):
    path = f'/repos/{registry["owner"]}/{project["repository"]}'
    repo = api.get(path)
    expected = f'{registry["owner"]}/{project["repository"]}'
    if not isinstance(repo, dict) or repo.get("private") is not False or repo.get("full_name", "").lower() != expected.lower():
        raise SourceError(404)
    base_url = "https://github.com/" + expected
    safe_url(repo["html_url"], base_url)
    record = {"status": "current", "checked_on": today, "url": base_url, "language": plain(repo.get("language") or "Not reported", 40), "updated_on": iso_date(repo.get("pushed_at")), "archived": bool(repo.get("archived")), "release": None, "workflow": None, "release_status": "current", "workflow_status": "current"}
    try:
        release = api.get(path + "/releases/latest")
        if not isinstance(release, dict):
            raise SourceError()
        if release.get("draft") is False and release.get("prerelease") is False:
            record["release"] = {"name": plain(release.get("name") or release["tag_name"], 90), "published_on": iso_date(release["published_at"]), "url": safe_url(release["html_url"], base_url + "/releases/")}
    except (SourceError, KeyError, TypeError, ValueError) as exc:
        if not isinstance(exc, SourceError) or exc.status != 404:
            record["release_status"] = "unavailable"
    try:
        query = urlencode({"branch": repo["default_branch"], "status": "completed", "per_page": 1})
        result = api.get(path + "/actions/runs?" + query)
        if not isinstance(result, dict) or not isinstance(result.get("workflow_runs"), list):
            raise SourceError()
        runs = result["workflow_runs"]
        if runs:
            run = runs[0]
            if not isinstance(run, dict):
                raise SourceError()
            conclusion = run.get("conclusion")
            if conclusion not in CONCLUSIONS:
                raise SourceError()
            record["workflow"] = {"name": plain(run["name"], 70), "conclusion": conclusion, "completed_on": iso_date(run["updated_at"]), "url": safe_url(run["html_url"], base_url + "/actions/runs/")}
    except (SourceError, KeyError, TypeError, ValueError):
        record["workflow_status"] = "unavailable"
    return record


def refresh(registry, previous, api, today):
    date.fromisoformat(today)
    snapshot = {"version": 1, "checked_on": today, "discovery_status": "current", "discovered": [], "projects": {}}
    try:
        snapshot["discovered"] = discover(registry, api)
    except SourceError:
        snapshot["discovered"] = copy.deepcopy(previous.get("discovered", []))
        snapshot["discovery_status"] = "stale"
    for project in registry["projects"] + snapshot["discovered"]:
        if project["visibility"] != "public":
            continue
        try:
            record = fetch_project(registry, project, api, today)
        except (SourceError, KeyError, TypeError, ValueError) as exc:
            old = previous.get("projects", {}).get(project["id"])
            if isinstance(exc, SourceError) and exc.status in {301, 302, 303, 307, 308, 403, 404, 410}:
                record = {"status": "unavailable", "checked_on": None}
            elif old and old.get("url"):
                record = copy.deepcopy(old)
                record["status"] = "stale"
            else:
                record = {"status": "unavailable", "checked_on": None}
        snapshot["projects"][project["id"]] = record
    validate_snapshot(registry, snapshot)
    return snapshot


def validate_snapshot(registry, snapshot):
    if set(snapshot) != {"version", "checked_on", "discovery_status", "discovered", "projects"} or snapshot["version"] != 1 or snapshot["discovery_status"] not in {"current", "stale"}:
        raise ValueError("Invalid snapshot structure")
    if snapshot["checked_on"]:
        date.fromisoformat(snapshot["checked_on"])
    combined = copy.deepcopy(registry)
    if any(p.get("visibility") != "public" or not p.get("id", "").startswith("auto-") for p in snapshot["discovered"]):
        raise ValueError("Discovery can contain only public opt-in projects")
    combined["projects"] += snapshot["discovered"]
    validate_registry(combined)
    public = {p["id"]: p for p in combined["projects"] if p["visibility"] == "public"}
    if set(snapshot["projects"]) - set(public):
        raise ValueError("Snapshot contains unregistered or private data")
    fields = {"status", "checked_on", "url", "language", "updated_on", "archived", "release", "workflow", "release_status", "workflow_status"}
    for pid, record in snapshot["projects"].items():
        if set(record) - fields or record.get("status") not in {"current", "stale", "unavailable"}:
            raise ValueError("Invalid source record")
        if record.get("status") == "unavailable" and set(record) != {"status", "checked_on"}:
            raise ValueError("Unavailable sources cannot retain remote metadata")
        if record.get("status") != "unavailable" and (set(record) != fields or not record.get("checked_on")):
            raise ValueError("Verified and cached records need complete source fields")
        if record.get("updated_on"):
            date.fromisoformat(record["updated_on"])
        if record.get("checked_on"):
            date.fromisoformat(record["checked_on"])
        if record.get("url"):
            base = f'https://github.com/{registry["owner"]}/{public[pid]["repository"]}'
            if record["url"] != base or record.get("release_status") not in {"current", "unavailable"} or record.get("workflow_status") not in {"current", "unavailable"}:
                raise ValueError("Invalid source provenance")
            for key, fields_, prefix in (("release", {"name", "published_on", "url"}, "/releases/"), ("workflow", {"name", "conclusion", "completed_on", "url"}, "/actions/runs/")):
                item = record.get(key)
                if item is not None:
                    if set(item) != fields_:
                        raise ValueError("Unexpected remote fields")
                    safe_url(item["url"], base + prefix)
                    plain(item["name"], 90)
                    item_date = item.get("published_on" if key == "release" else "completed_on")
                    if not item_date:
                        raise ValueError("Evidence needs a source date")
                    date.fromisoformat(item_date)
                    if key == "workflow" and item["conclusion"] not in CONCLUSIONS:
                        raise ValueError("Invalid workflow conclusion")
    return snapshot


def text(x, y, value, size=18, color="#e6edf3", weight=400, family="Segoe UI,Arial,sans-serif"):
    return f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" font-weight="{weight}" fill="{color}">{html.escape(str(value))}</text>'


def palette(theme):
    return {"bg": "#091221", "panel": "#122137", "border": "#2a405c", "ink": "#f1f5f9", "muted": "#b4c5d9", "accent": "#7dd3fc", "violet": "#bba5ff"} if theme == "dark" else {"bg": "#f3f6ff", "panel": "#ffffff", "border": "#cbd5e1", "ink": "#142238", "muted": "#475569", "accent": "#0369a1", "violet": "#6d28d9"}


def category_color(category, theme):
    return COLORS[category] if theme == "dark" else {"data": "#0369a1", "ai": "#6d28d9", "markets": "#047857", "engineering": "#92400e"}[category]


def visual_defs(theme, accent=None):
    p = palette(theme)
    accent = accent or p["accent"]
    return (f'<defs><linearGradient id="surface" x2="1" y2="1"><stop stop-color="{p["panel"]}"/><stop offset="1" stop-color="{p["bg"]}"/></linearGradient>'
            f'<linearGradient id="rim"><stop stop-color="{accent}" stop-opacity=".55"/><stop offset=".5" stop-color="{p["border"]}"/><stop offset="1" stop-color="{p["violet"]}" stop-opacity=".45"/></linearGradient>'
            f'<linearGradient id="highlight"><stop stop-color="{accent}"/><stop offset="1" stop-color="{p["violet"]}"/></linearGradient>'
            f'<radialGradient id="aura"><stop stop-color="{accent}" stop-opacity="{.20 if theme == "dark" else .10}"/><stop offset="1" stop-color="{accent}" stop-opacity="0"/></radialGradient></defs>')


def icon(category, x, y, color, size=28, opacity=1):
    shapes = {
        "data": '<path d="M4 7 Q4 3 16 3 Q28 3 28 7 Q28 11 16 11 Q4 11 4 7 Z M4 7 V24 Q4 28 16 28 Q28 28 28 24 V7 M4 15 Q4 19 16 19 Q28 19 28 15 M4 22 Q4 26 16 26 Q28 26 28 22"/>',
        "ai": '<path d="M8 8 L24 8 L16 24 Z M16 16 V24 M8 8 L16 16 L24 8"/><circle cx="8" cy="8" r="4"/><circle cx="24" cy="8" r="4"/><circle cx="16" cy="24" r="4"/>',
        "markets": '<rect x="3" y="16" width="6" height="12" rx="1"/><rect x="13" y="5" width="6" height="23" rx="1"/><rect x="23" y="11" width="6" height="17" rx="1"/>',
        "engineering": '<path d="M10 6 L3 16 L10 26 M22 6 L29 16 L22 26 M19 4 L13 28"/>',
    }
    return f'<g transform="translate({x} {y}) scale({size / 32})" fill="none" stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" opacity="{opacity}">{shapes[category]}</g>'


def svg(width, height, title, description, body, theme, background=True):
    p = palette(theme)
    backdrop = f'<rect width="{width}" height="{height}" rx="24" fill="{p["bg"]}"/>' if background else ""
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc"><title id="title">{html.escape(title)}</title><desc id="desc">{html.escape(description)}</desc>{backdrop}{body}</svg>\n'


def banner(registry, snapshot, theme, mobile=False):
    p, profile = palette(theme), registry["profile"]
    public = len([q for q in registry["projects"] + snapshot["discovered"] if q["visibility"] == "public"])
    private = len([q for q in registry["projects"] if q["visibility"] == "summary-only"])
    width, height = (360, 350) if mobile else (1180, 378)
    body = visual_defs(theme)
    body += f'<rect x="1" y="1" width="{width - 2}" height="{height - 2}" rx="24" fill="url(#surface)" stroke="url(#rim)"/>'
    body += f'<rect x="{width - (260 if mobile else 470)}" y="10" width="{250 if mobile else 460}" height="{height - 20}" rx="24" fill="url(#aura)"/>'
    if mobile:
        body += icon("ai", 305, 22, p["violet"], 29)
        body += text(24, 40, "ENGINEERING / RESEARCH", 12, p["accent"], 600)
        body += text(24, 88, profile["name"], 26, p["ink"], 700)
        body += text(24, 120, profile["role"], 17, p["muted"])
        body += '<path d="M26 148 H166 L190 158 H334" fill="none" stroke="url(#highlight)" stroke-opacity=".5"/>'
        for x in (26, 166, 334):
            body += f'<circle cx="{x}" cy="{148 if x != 334 else 158}" r="3" fill="{p["accent"]}"/>'
        body += text(24, 193, profile["direction"], 25, p["ink"], 600)
        body += text(24, 225, profile["location"], 15, p["muted"])
        body += '<rect x="24" y="246" width="312" height="66" rx="14" fill="url(#aura)" stroke="url(#rim)"/>'
        body += text(40, 272, f"{public} public projects", 16, p["ink"], 600)
        body += text(40, 296, f"{private} curated private summaries", 15, p["muted"])
        body += text(24, 333, "PUBLIC EVIDENCE. CURATED SCOPE.", 10, p["muted"], 600)
        return svg(width, height, profile["name"], f'{profile["role"]}. {profile["direction"]}. {public} public projects and {private} curated private summaries. Decorative nodes represent shared skills.', body, theme, background=False)
    for x in range(790, 1150, 32):
        for y in range(30, 300, 32):
            body += f'<circle cx="{x}" cy="{y}" r="1.3" fill="{p["border"]}" opacity=".7"/>'
    body += '<circle cx="1010" cy="167" r="130" fill="none" stroke="url(#rim)"/><circle cx="1010" cy="167" r="93" fill="none" stroke="url(#highlight)" stroke-opacity=".35" stroke-dasharray="4 8"/>'
    body += '<path d="M916 83 L1010 167 L1123 166 M1010 167 L954 281" fill="none" stroke="url(#highlight)" stroke-opacity=".5" stroke-width="2"/>'
    body += f'<circle cx="1010" cy="167" r="51" fill="{p["panel"]}" stroke="url(#rim)"/>'
    body += text(976, 183, "AS", 43, p["ink"], 700)
    for category, x, y in (("data", 916, 83), ("ai", 1123, 166), ("markets", 954, 281)):
        color = category_color(category, theme)
        body += f'<circle cx="{x}" cy="{y}" r="24" fill="{p["panel"]}" stroke="{color}" stroke-opacity=".6"/>'
        body += icon(category, x - 12, y - 12, color, 24)
    body += text(44, 49, "ENGINEERING / RESEARCH / PRACTICAL SYSTEMS", 14, p["accent"], 600)
    body += text(44, 116, profile["name"], 49, p["ink"], 700)
    body += text(46, 156, profile["role"], 22, p["muted"])
    body += text(44, 226, profile["direction"], 32, p["ink"], 600)
    body += text(46, 268, profile["location"], 17, p["muted"])
    for x, chip_width, label in ((242, 172, f"{public} public projects"), (428, 259, f"{private} curated private summaries")):
        body += f'<rect x="{x}" y="245" width="{chip_width}" height="34" rx="17" fill="url(#aura)" stroke="url(#rim)"/>'
        body += text(x + 16, 267, label, 15, p["ink"])
    body += '<rect x="44" y="321" width="1092" height="2" rx="1" fill="url(#highlight)"/>'
    body += text(44, 354, "BUILD USEFUL SYSTEMS. MEASURE WHAT THEY DO.", 13, p["muted"], 600)
    return svg(width, height, profile["name"], f'{profile["role"]}. {profile["direction"]}. Decorative nodes represent shared skills, not financial results or runtime dependencies.', body, theme, background=False)


def card(project, record, theme, mobile=False):
    p = palette(theme)
    accent = category_color(project["category"], theme)
    width, height = (360, 398) if mobile else (580, 326)
    body = visual_defs(theme, accent)
    body += f'<rect x="1" y="1" width="{width - 2}" height="{height - 2}" rx="23" fill="url(#surface)" stroke="url(#rim)"/>'
    body += f'<rect x="{width - 161}" y="3" width="158" height="168" rx="22" fill="url(#aura)"/>'
    body += f'<path d="M25 2 H{width - 25}" stroke="url(#highlight)" stroke-width="2" stroke-opacity=".65"/>'
    body += f'<rect x="{width - 61}" y="19" width="39" height="39" rx="12" fill="{p["panel"]}" stroke="{accent}" stroke-opacity=".45"/>'
    body += icon(project["category"], width - 53, 27, accent, 23)
    body += f'<circle cx="28" cy="39" r="3" fill="{accent}"/>'
    body += text(40, 44, CATEGORIES[project["category"]].upper(), 12 if mobile else 14, p["muted"], 600)
    lines = textwrap.wrap(project["name"], 23 if mobile else 32, break_long_words=True)
    for i, line in enumerate(lines[:2]):
        body += text(26, 84 + i * 29, line, 26, p["ink"], 700)
    description_y = 121 if len(lines) == 1 else 145
    all_summary_lines = textwrap.wrap(project["summary"], 35 if mobile else 54, break_long_words=True)
    summary_lines = all_summary_lines[:4 if mobile else 3]
    if len(summary_lines) < len(all_summary_lines):
        summary_lines[-1] = summary_lines[-1].rstrip(" .") + "…"
    for i, line in enumerate(summary_lines):
        body += text(26, description_y + i * 24, line, 17 if mobile else 18, p["muted"])
    stack = " · ".join(project["stack"])[:72] or record.get("language", "Public source")
    for i, line in enumerate(textwrap.wrap(stack, 38 if mobile else 74, break_long_words=False, break_on_hyphens=False)[:2 if mobile else 1]):
        body += text(26, (250 if mobile else 221) + i * 18, line, 14, p["muted"])
    body += f'<path d="M26 {295 if mobile else 245} H{width - 28}" stroke="url(#rim)"/>'
    if project["visibility"] == "summary-only":
        label, detail = "PRIVATE / CURATED SUMMARY", "Published scope only · no private repository access"
    elif record.get("status") == "current":
        label = "ARCHIVED PUBLIC SOURCE" if record.get("archived") else "PUBLIC SOURCE / CHECKED " + record["checked_on"]
        if record.get("release"):
            detail = "Release: " + record["release"]["name"]
        else:
            detail = "Repository updated " + (record.get("updated_on") or "date unavailable")
    elif record.get("status") == "stale":
        label, detail = "CACHED / SOURCE CHECK FAILED", "Last verified " + (record.get("checked_on") or "date unavailable")
    else:
        label, detail = "SOURCE UNAVAILABLE", "Remote links and metrics withheld until verification"
    body += text(26, 324 if mobile else 277, label, 13 if mobile else 16, accent, 600)
    for i, line in enumerate(textwrap.wrap(plain(detail, 70), 40 if mobile else 74)[:2 if mobile else 1]):
        body += text(26, (350 if mobile else 303) + i * 18, line, 14, p["muted"])
    return svg(width, height + 12, project["name"], project["summary"] + " " + label + ". " + detail, body, theme, background=False)


def project_map(projects, theme, mobile=False):
    p = palette(theme)
    body = visual_defs(theme)
    body += text(24 if mobile else 36, 38 if mobile else 46, "ONE PORTFOLIO." if mobile else "ONE PORTFOLIO. FOUR CONNECTED THEMES.", 22, p["ink"], 700)
    body += text(24 if mobile else 36, 69 if mobile else 77, "Four themes. Shared skills." if mobile else "A map of interests and shared skills", 16, p["muted"])
    for i, (category, label) in enumerate(CATEGORIES.items()):
        x, y = (24, 94 + i * 134) if mobile else (36 + i * 288, 105)
        accent = category_color(category, theme)
        members = [q for q in projects if q["category"] == category]
        body += f'<rect x="{x}" y="{y}" width="{312 if mobile else 258}" height="{114 if mobile else 194}" rx="16" fill="url(#surface)" stroke="{accent}" stroke-opacity=".35"/>'
        body += f'<path d="M{x + 17} {y + 1} H{x + (294 if mobile else 240)}" stroke="{accent}" stroke-opacity=".65"/>'
        body += f'<rect x="{x + 16}" y="{y + 17}" width="36" height="36" rx="11" fill="{p["bg"]}" stroke="{accent}" stroke-opacity=".4"/>'
        body += icon(category, x + 22, y + 23, accent, 24)
        if not mobile:
            body += text(x + 217, y + 41, f"0{i + 1}", 14, p["muted"], 600)
        body += text(x + (64 if mobile else 17), y + (34 if mobile else 85), label, 16 if mobile else 17, p["ink"], 600)
        body += text(x + (64 if mobile else 17), y + (61 if mobile else 115), f"{len(members)} projects", 14 if mobile else 15, accent, 600)
        names = " + ".join(q["name"].split()[0] for q in members)
        for j, line in enumerate(textwrap.wrap(names, 40 if mobile else 29)[:2]):
            body += text(x + 17, y + (87 if mobile else 151) + j * 16, line, 13, p["muted"])
    return svg(360 if mobile else 1200, 634 if mobile else 331, "Portfolio theme map", "; ".join(f'{label}: ' + ", ".join(q["name"] for q in projects if q["category"] == key) for key, label in CATEGORIES.items()) + ". Themes do not imply runtime dependencies. Category icons are decorative.", body, theme)


def picture(name, alt, width="100%"):
    escaped = html.escape(alt, quote=True)
    return f'<picture>\n  <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="assets/portfolio/{name}-mobile-dark.svg">\n  <source media="(max-width: 600px) and (prefers-color-scheme: light)" srcset="assets/portfolio/{name}-mobile-light.svg">\n  <source media="(prefers-color-scheme: dark)" srcset="assets/portfolio/{name}-dark.svg">\n  <source media="(prefers-color-scheme: light)" srcset="assets/portfolio/{name}-light.svg">\n  <img src="assets/portfolio/{name}-light.svg" width="{width}" alt="{escaped}">\n</picture>'


def link(url, label):
    return f'<a href="{html.escape(safe_url(url), quote=True)}">{html.escape(label).replace("|", "&#124;")}</a>'


def render(registry, snapshot, readme):
    validate_registry(registry)
    validate_snapshot(registry, snapshot)
    if readme.count(START) != 1 or readme.count(END) != 1 or readme.index(START) >= readme.index(END):
        raise ValueError("README must contain one ordered generated block")
    projects = registry["projects"] + snapshot["discovered"]
    outputs = {}
    for theme in ("dark", "light"):
        outputs[f"assets/portfolio/banner-{theme}.svg"] = banner(registry, snapshot, theme)
        outputs[f"assets/portfolio/map-{theme}.svg"] = project_map(projects, theme)
        outputs[f"assets/portfolio/banner-mobile-{theme}.svg"] = banner(registry, snapshot, theme, mobile=True)
        outputs[f"assets/portfolio/map-mobile-{theme}.svg"] = project_map(projects, theme, mobile=True)
        for project in projects:
            outputs[f'assets/portfolio/{project["id"]}-{theme}.svg'] = card(project, snapshot["projects"].get(project["id"], {}), theme)
            outputs[f'assets/portfolio/{project["id"]}-mobile-{theme}.svg'] = card(project, snapshot["projects"].get(project["id"], {}), theme, mobile=True)
    current = sum(r["status"] == "current" for r in snapshot["projects"].values())
    public_count = len([p for p in projects if p["visibility"] == "public"])
    check = snapshot["checked_on"] or "not yet checked"
    parts = [picture("banner", registry["profile"]["name"] + " — Data to AI to Decisions"), "", f'<p align="center">Public sources checked on <strong>{html.escape(check)} UTC</strong> · {current}/{public_count} source checks succeeded</p>', "", picture("map", "Project themes: data, AI, markets and engineering"), "", '## Selected work · مشاريع مختارة', "", '<p align="center">']
    public = sorted([q for q in projects if q["visibility"] == "public"], key=lambda q: not q["featured"])
    for project in public:
        record = snapshot["projects"].get(project["id"], {})
        image = picture(project["id"], project["name"] + ": " + project["summary"], "410")
        if record.get("url"):
            image = f'<a href="{html.escape(record["url"], quote=True)}">{image}</a>'
        parts += [image]
    parts += ['</p>', '']
    releases = [(project, snapshot["projects"].get(project["id"], {})) for project in public]
    releases = [(q, r) for q, r in releases if r.get("release") and r["status"] == "current"]
    releases.sort(key=lambda pair: pair[1]["release"]["published_on"] or "", reverse=True)
    parts += ['### Published releases · آخر الإصدارات', ""]
    if releases:
        for project, record in releases[:4]:
            release = record["release"]
            parts += [f'- **{html.escape(project["name"])}** — {link(release["url"], release["name"])} · {release["published_on"]}', ""]
    else:
        parts += ["No published stable releases were verified in this snapshot. Repository activity remains visible on each source card.", ""]
    parts += ['<details>', '<summary>Source evidence and synchronization status</summary>', '', '| Project | Source check | Latest stable release | Latest completed workflow on default branch |', '|:---|:---|:---|:---|']
    for project in public:
        record = snapshot["projects"].get(project["id"], {"status": "unavailable"})
        workflow = record.get("workflow")
        release = record.get("release")
        release_evidence = link(release["url"], release["name"]) if release else ("Could not verify" if record.get("release_status") == "unavailable" else "No stable release reported")
        evidence = link(workflow["url"], f'{workflow["name"]}: {workflow["conclusion"]}') + " · " + str(workflow["completed_on"]) if workflow else ("Could not verify" if record.get("workflow_status") == "unavailable" else "No completed run reported")
        if record["status"] != "current":
            evidence = "Cached: " + evidence if record["status"] == "stale" else "Source unavailable"
            release_evidence = "Cached: " + release_evidence if record["status"] == "stale" else "Source unavailable"
        project_label = link(record["url"], project["name"]) if record.get("url") else html.escape(project["name"]).replace("|", "&#124;")
        parts.append(f'| {project_label} | {record["status"]} · {record.get("checked_on") or "unverified"} | {release_evidence} | {evidence} |')
    parts += ['', "A workflow result describes that workflow only. Project scope, model calibration, production readiness and financial performance require their own evidence.", '', f'Discovery check: **{snapshot["discovery_status"]}**. Add the `{registry["discovery_topic"]}` topic to an eligible public repository to include it automatically.', '', '</details>', '', '## Private work · ملخصات الأعمال الخاصة', '', 'These curated summaries reuse scope already published in this profile. Private repositories are not queried.', '', '<p align="center">']
    for project in projects:
        if project["visibility"] == "summary-only":
            parts += [picture(project["id"], project["name"] + ": " + project["summary"], "410")]
    parts += ['</p>', '']
    generated = START + "\n\n" + "\n".join(parts).rstrip() + "\n\n" + END
    before, remainder = readme.split(START)
    _, after = remainder.split(END)
    outputs["README.md"] = before + generated + after
    outputs["portfolio/snapshot.json"] = json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    for name, content in outputs.items():
        if name.endswith(".svg"):
            root = ET.fromstring(content)
            if any(el.tag.rsplit("}", 1)[-1] not in {"svg", "title", "desc", "rect", "circle", "path", "text", "g", "defs", "linearGradient", "radialGradient", "stop"} for el in root.iter()):
                raise ValueError("Unexpected SVG content")
    return outputs


def write_outputs(root, outputs, check=False):
    generated = root / "assets" / "portfolio"
    existing = {p.relative_to(root).as_posix() for p in generated.glob("*.svg")} if generated.exists() else set()
    removed = existing - set(outputs)
    changed = [name for name, content in outputs.items() if not (root / name).exists() or (root / name).read_text(encoding="utf-8") != content]
    if check:
        if changed or removed:
            raise ValueError("Generated files need refresh: " + ", ".join(sorted(changed + list(removed))))
        return
    for name in changed + list(removed):
        target = root / name
        if not target.resolve().is_relative_to(root.resolve()) or target.is_symlink():
            raise ValueError("Unsafe output path")
    for name in changed:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_text(outputs[name], encoding="utf-8", newline="\n")
        temporary.replace(target)
    for name in removed:
        target = root / name
        if target.resolve().parent != generated.resolve() or target.is_symlink():
            raise ValueError("Unsafe generated file cleanup")
        target.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--refresh", action="store_true", help="Fetch public GitHub metadata")
    mode.add_argument("--check", action="store_true", help="Verify deterministic generated output offline")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        registry = validate_registry(json.loads((root / "portfolio/projects.json").read_text(encoding="utf-8")))
        cache = root / "portfolio/snapshot.json"
        snapshot = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {"version": 1, "checked_on": None, "discovery_status": "stale", "discovered": [], "projects": {}}
        validate_snapshot(registry, snapshot)
        if args.refresh:
            snapshot = refresh(registry, snapshot, GitHub(), datetime.now(timezone.utc).date().isoformat())
        outputs = render(registry, snapshot, (root / "README.md").read_text(encoding="utf-8"))
        write_outputs(root, outputs, args.check)
        states = [r["status"] for r in snapshot["projects"].values()]
        print(f'Portfolio {"verified" if args.check else "rendered"}: {len(outputs)} files; {states.count("current")} current, {states.count("stale")} cached, {states.count("unavailable")} unavailable sources.')
    except (ValueError, KeyError, TypeError, OSError, ET.ParseError):
        print("Portfolio validation failed. Check registry, snapshot and generated-block structure; no source payloads are logged.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
