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
    return ({"bg": "#0B1220", "panel": "#111D30", "border": "#2C394D",
             "ink": "#F4F1EA", "muted": "#B7C0CE", "accent": "#D6B87C"}
            if theme == "dark" else
            {"bg": "#FAF8F3", "panel": "#FFFFFF", "border": "#E1D9C8",
             "ink": "#17243A", "muted": "#526074", "accent": "#826226"})


def svg(width, height, title, description, body, theme):
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc"><title id="title">{html.escape(title)}</title><desc id="desc">{html.escape(description)}</desc>{body}</svg>\n'


def metal_defs():
    return ('<defs>'
            '<linearGradient id="gold" x1="0" y1="0" x2="1" y2="1">'
            '<stop stop-color="#F1D59A"/><stop offset=".55" stop-color="#D6B87C"/>'
            '<stop offset="1" stop-color="#A78243"/></linearGradient>'
            '<linearGradient id="silver" x1="0" y1="0" x2="1" y2=".7">'
            '<stop stop-color="#F4F1EA"/><stop offset=".4" stop-color="#AAA9A4"/>'
            '<stop offset="1" stop-color="#414854"/></linearGradient>'
            '<linearGradient id="ivory" x1="0" y1="0" x2="1" y2="1">'
            '<stop stop-color="#FCF8EC"/><stop offset="1" stop-color="#B5B2A7"/>'
            '</linearGradient></defs>')


def folded_mark(x, y, scale):
    """Original directional mark: three interleaved, folded planes."""
    return (f'<g transform="translate({x} {y}) scale({scale})">'
            '<path d="M184 25 Q184 13 195 19 L281 68 Q292 74 292 86 V195 '
            'Q292 213 278 222 L184 276 Z" fill="url(#gold)" stroke="#F1D59A" stroke-width="1.4"/>'
            '<path d="M105 83 Q105 71 116 77 L224 139 Q235 145 235 158 V249 '
            'L118 182 Q105 176 105 163 Z" fill="url(#silver)" stroke="#E7E2D7" stroke-width="1.4"/>'
            '<path d="M235 158 L278 183 Q292 192 292 201 Q292 213 278 222 '
            'L184 276 H111 L184 234 V188 Z" fill="url(#gold)"/>'
            '<path d="M235 158 V249 L184 276 V188 Z" fill="#0B1220" fill-opacity=".20"/>'
            '<path d="M105 163 L155 192 L76 237 Q71 240 64 239 L4 237 '
            'Q-2 237 4 233 Z" fill="url(#ivory)" stroke="#E7E2D7" stroke-width="1"/>'
            '<path d="M155 192 V215 L76 260 V237 Z" fill="#303B4D"/>'
            '<path d="M155 192 L76 237" fill="none" stroke="#F4F1EA" stroke-opacity=".6"/>'
            '</g>')


def banner(registry, snapshot, theme, mobile=False):
    p, profile = palette(theme), registry["profile"]
    width, x = (360, 24) if mobile else (900, 48)
    given, _, family = profile["name"].partition(" ")
    name_lines = textwrap.wrap(given, 13 if mobile else 17)
    name_lines += textwrap.wrap(family, 15 if mobile else 19)
    role_lines = textwrap.wrap(profile["role"], 25 if mobile else 43)
    direction_lines = textwrap.wrap(profile["direction"], 23 if mobile else 40)
    name_y, name_step = (163, 52) if mobile else (120, 70)
    role_y = name_y + (len(name_lines) - 1) * name_step + (48 if mobile else 52)
    location_y = role_y + (len(role_lines) - 1) * 32 + 42
    direction_y = location_y + (60 if mobile else 72)
    height = direction_y + (len(direction_lines) - 1) * 35 + 34
    body = metal_defs() + f'<rect width="{width}" height="{height}" fill="{p["bg"]}"/>'
    body += f'<path d="M{x} {34 if mobile else 44} H{x + 64}" stroke="{p["accent"]}" stroke-width="4"/>'
    if mobile:
        body += text(x, 88, "DATA / AI", 24, p["accent"], 600)
        body += folded_mark(221, 16, .38)
    else:
        body += folded_mark(578, 47, .96)
    for i, line in enumerate(name_lines):
        body += text(x, name_y + i * name_step, line, 42 if mobile else 62, p["ink"], 700)
    for i, line in enumerate(role_lines):
        body += text(x, role_y + i * 32, line, 24 if mobile else 26, p["ink"])
    body += text(x + 28, location_y, profile["location"], 24 if mobile else 26, p["muted"])
    body += (f'<g transform="translate({x + 1} {location_y - 20})" fill="none" stroke="{p["accent"]}" stroke-width="2">'
             '<path d="M9 23 Q0 12 0 9 A9 9 0 1 1 18 9 Q18 12 9 23Z"/><circle cx="9" cy="9" r="2.5"/></g>')
    body += f'<path d="M{x} {direction_y - 23} V{direction_y + (len(direction_lines) - 1) * 35 + 2}" stroke="{p["accent"]}" stroke-width="4"/>'
    for i, line in enumerate(direction_lines):
        body += text(x + 16, direction_y + i * 35, line, 26 if mobile else 30, p["ink"], 600)
    return svg(width, height, profile["name"], f'{profile["role"]}. {profile["direction"]}. {profile["location"]}. An original folded-plane mark represents connected engineering disciplines.', body, theme)


def source_notice(record):
    if record.get("status") == "stale":
        return "CACHED / SOURCE CHECK FAILED"
    if record.get("status") != "current":
        return "SOURCE UNAVAILABLE"
    return "ARCHIVED PUBLIC SOURCE" if record.get("archived") else ""


def card(project, record, theme, mobile=False, rank=1, last=False):
    p = palette(theme)
    width, margin = (360, 24) if mobile else (900, 48)
    title_x = 132 if mobile else 172
    title_lines = textwrap.wrap(project["name"], 14 if mobile else 40)
    summary_lines = textwrap.wrap(project["summary"], 26 if mobile else 53)
    body = metal_defs()
    bx, by = margin, 32
    body += f'<path d="M{bx + 21} {by} H{bx + 84} L{bx + 63} {by + 42} H{bx} Z" fill="url(#gold)"/>'
    body += text(bx + 25, by + 30, f"{rank:02}", 27, "#0B1220", 600)
    title_y = 61
    for i, line in enumerate(title_lines):
        body += text(title_x, title_y + i * 36, line, 28 if mobile else 34, p["ink"], 700)
    title_bottom = title_y + (len(title_lines) - 1) * 36
    divider_x = 116 if mobile else 151
    body += f'<path d="M{divider_x} 32 V{title_bottom + 10}" stroke="{p["accent"]}" stroke-opacity=".65"/>'
    y = title_bottom + 42
    copy_x = margin if mobile else title_x
    for line in summary_lines:
        body += text(copy_x, y, line, 24 if mobile else 26, p["muted"])
        y += 31
    notice = source_notice(record)
    if notice:
        y += 8
        for line in textwrap.wrap(notice, 25 if mobile else 50):
            body += text(copy_x, y, line, 24, p["accent"], 600)
            y += 31
    height = y + (84 if last else 10)
    if last:
        for i in range(4):
            offset = i * 9
            body += (f'<path d="M{width * .15} {height - 4 + offset} '
                     f'C{width * .42} {height - 40 + offset} {width * .56} {height - 18 + offset} {width * .68} {height - 37 + offset} '
                     f'L{width + 30} {y + 12 + offset}" fill="none" stroke="{p["accent"]}" '
                     f'stroke-opacity="{.75 - i * .15}" stroke-width="1"/>')
    else:
        body += f'<path d="M{margin} {height - 1} H{width - margin}" stroke="{p["border"]}"/>'
    frame = f'<rect width="{width}" height="{height}" fill="{p["bg"]}"/>'
    return svg(width, height, project["name"], project["summary"] + (" " + notice if notice else ""), frame + body, theme)


def picture(name, alt, width="900"):
    escaped = html.escape(alt, quote=True)
    return f'<picture>\n  <source media="(max-width: 900px) and (prefers-color-scheme: dark)" srcset="assets/portfolio/{name}-mobile-dark.svg">\n  <source media="(max-width: 900px) and (prefers-color-scheme: light)" srcset="assets/portfolio/{name}-mobile-light.svg">\n  <source media="(prefers-color-scheme: dark)" srcset="assets/portfolio/{name}-dark.svg">\n  <source media="(prefers-color-scheme: light)" srcset="assets/portfolio/{name}-light.svg">\n  <img src="assets/portfolio/{name}-light.svg" width="{width}" alt="{escaped}">\n</picture>'


def link(url, label):
    return f'<a href="{html.escape(safe_url(url), quote=True)}">{html.escape(label).replace("|", "&#124;")}</a>'


def render(registry, snapshot, readme):
    validate_registry(registry)
    validate_snapshot(registry, snapshot)
    if readme.count(START) != 1 or readme.count(END) != 1 or readme.index(START) >= readme.index(END):
        raise ValueError("README must contain one ordered generated block")
    projects = registry["projects"] + snapshot["discovered"]
    public = [q for q in projects if q["visibility"] == "public"]
    featured = [q for q in public if q["featured"]][:3]
    featured_ids = {q["id"] for q in featured}
    secondary = [q for q in public if q["id"] not in featured_ids]
    outputs = {}
    for theme in ("dark", "light"):
        outputs[f"assets/portfolio/banner-{theme}.svg"] = banner(registry, snapshot, theme)
        outputs[f"assets/portfolio/banner-mobile-{theme}.svg"] = banner(registry, snapshot, theme, mobile=True)
        for rank, project in enumerate(featured, 1):
            record = snapshot["projects"].get(project["id"], {})
            outputs[f'assets/portfolio/{project["id"]}-{theme}.svg'] = card(project, record, theme, rank=rank, last=rank == len(featured))
            outputs[f'assets/portfolio/{project["id"]}-mobile-{theme}.svg'] = card(project, record, theme, mobile=True, rank=rank, last=rank == len(featured))
    current = sum(r["status"] == "current" for r in snapshot["projects"].values())
    public_count = len([p for p in projects if p["visibility"] == "public"])
    check = snapshot["checked_on"] or "not yet checked"
    parts = ['## Selected Work · أعمال مختارة', '', '<p>'] if featured else []
    for project in featured:
        record = snapshot["projects"].get(project["id"], {})
        notice = source_notice(record)
        image = picture(project["id"], project["name"] + ": " + project["summary"] + (" " + notice if notice else ""))
        if record.get("url"):
            image = f'<a href="{html.escape(record["url"], quote=True)}">{image}</a>'
        # One HTML block preserves picture sources and links without the large
        # paragraph gaps that would separate the continuous project rows.
        parts += [image]
    if featured:
        parts += ['</p>', '']
    if secondary:
        parts += ['<details>', '<summary><strong>More Projects · مشاريع أخرى</strong></summary>', '']
        for project in secondary:
            record = snapshot["projects"].get(project["id"], {})
            label = link(record["url"], project["name"]) if record.get("url") else html.escape(project["name"])
            notice = source_notice(record)
            suffix = f' <em>{html.escape(notice)}</em>' if notice else ''
            parts += [f'<p><strong>{label}</strong> — {html.escape(project["summary"])}{suffix}</p>', '']
        parts += ['</details>', '']
    private = [q for q in projects if q["visibility"] == "summary-only"]
    if private:
        parts += ['<details>', '<summary><strong>Private Work · أعمال خاصة</strong></summary>', '',
                  'Published summaries only. Private repositories are not queried.', '']
        for project in private:
            parts += [f'<p><strong>{html.escape(project["name"])}</strong> — {html.escape(project["summary"])}</p>', '']
        parts += ['</details>', '']
    parts += ['<details>', '<summary><strong>Source Details · المصادر والتحديثات</strong></summary>', '',
              f'Public sources checked on **{html.escape(check)} UTC** · {current}/{public_count} source checks succeeded.', '']
    for project in featured:
        if project["stack"]:
            parts += [f'<p><strong>{html.escape(project["name"])}</strong> · {html.escape(" · ".join(project["stack"]))}</p>', '']
    releases = [(project, snapshot["projects"].get(project["id"], {})) for project in public]
    releases = [(q, r) for q, r in releases if r.get("release") and r["status"] == "current"]
    releases.sort(key=lambda pair: pair[1]["release"]["published_on"] or "", reverse=True)
    if releases:
        parts += ['### Published releases · آخر الإصدارات', '']
        for project, record in releases[:4]:
            release = record["release"]
            parts += [f'- **{html.escape(project["name"])}** — {link(release["url"], release["name"])} · {release["published_on"]}', ""]
    parts += ['| Project | Source check | Latest stable release | Latest completed workflow on default branch |', '|:---|:---|:---|:---|']
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
    parts += ['', "A workflow result describes that workflow only. Project scope, model calibration, production readiness and financial performance require their own evidence.", '', f'Discovery check: **{snapshot["discovery_status"]}**. Add the `{registry["discovery_topic"]}` topic to an eligible public repository to include it automatically.', '', '</details>', '']
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
