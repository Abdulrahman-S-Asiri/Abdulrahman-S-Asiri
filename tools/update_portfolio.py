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
    return ({"bg": "#0D1117", "panel": "#151C29", "border": "#2C384A",
             "ink": "#F0F6FC", "muted": "#A7B4C5", "accent": "#58D5F7", "violet": "#B69BFF"}
            if theme == "dark" else
            {"bg": "#F6F8FC", "panel": "#FFFFFF", "border": "#CDD6E3",
             "ink": "#152339", "muted": "#526074", "accent": "#096A82", "violet": "#6942AF"})


def svg(width, height, title, description, body, theme):
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc"><title id="title">{html.escape(title)}</title><desc id="desc">{html.escape(description)}</desc>{body}</svg>\n'


def motion_style():
    # A single short entrance, never an endless distraction. Images remain useful
    # with CSS/animation disabled, including GitHub's static preview contexts.
    return ('<style>@keyframes trace{from{stroke-dashoffset:120}to{stroke-dashoffset:0}}'
            '.trace{stroke-dasharray:8 12;animation:trace 4.5s ease-out 1}'
            '@media(prefers-reduced-motion:reduce){.trace{animation:none}}</style>')


def banner(registry, snapshot, theme, mobile=False):
    p, profile = palette(theme), registry["profile"]
    width, height, x = (360, 350, 24) if mobile else (900, 240, 40)
    body = motion_style() + f'<rect width="{width}" height="{height}" rx="16" fill="{p["bg"]}"/>'
    body += f'<path d="M{x} 24 H{x + 42}" stroke="{p["accent"]}" stroke-width="3"/>'
    body += text(x + 54, 31, "DATA / AI", 21, p["accent"], 600)
    given, _, family = profile["name"].partition(" ")
    name_lines = (textwrap.wrap(given, 15) + textwrap.wrap(family, 18)) if mobile else [profile["name"]]
    name_y, step = (86, 43) if mobile else (102, 57)
    for i, line in enumerate(name_lines):
        body += text(x, name_y + i * step, line, 35 if mobile else 43, p["ink"], 700)
    y = name_y + (len(name_lines) - 1) * step + 41
    for line in textwrap.wrap(profile["role"], 25 if mobile else 43):
        body += text(x, y, line, 24 if mobile else 25, p["muted"])
        y += 30
    body += text(x, y + 7, profile["location"], 22, p["muted"])
    if mobile:
        body += f'<path class="trace" d="M44 289 H316" stroke="{p["accent"]}" stroke-width="2"/>'
        for cx, label in ((44, "DATA"), (180, "MODELS"), (316, "BUILD")):
            body += f'<circle cx="{cx}" cy="289" r="7" fill="{p["bg"]}" stroke="{p["violet"]}" stroke-width="2"/>'
            tx = cx - (22 if label == "DATA" else 40 if label == "MODELS" else 27)
            body += text(tx, 326, label, 19, p["muted"], 600)
    else:
        # A compact data-flow illustration, not a fabricated metric or dashboard.
        for gx in range(604, 867, 24):
            for gy in range(36, 263, 24):
                body += f'<circle cx="{gx}" cy="{gy}" r="1" fill="{p["border"]}"/>'
        body += f'<path class="trace" d="M646 82 H715 V146 H790 V211 H830" fill="none" stroke="{p["accent"]}" stroke-width="3"/>'
        for bx, by, label in ((590, 52, "DATA"), (677, 116, "MODELS"), (744, 181, "SYSTEMS")):
            body += f'<g class="reveal"><rect x="{bx}" y="{by}" width="116" height="54" rx="10" fill="{p["panel"]}" stroke="{p["border"]}"/>'
            body += text(bx + 15, by + 34, label, 20, p["accent"] if label != "MODELS" else p["violet"], 600) + '</g>'
        body += text(x, 218, profile["direction"], 23, p["accent"], 600)
    return svg(width, height, profile["name"], f'{profile["role"]}. {profile["direction"]}. {profile["location"]}. Data, models and systems illustration.', body, theme)


def source_notice(record):
    if record.get("status") == "stale":
        return "CACHED / SOURCE CHECK FAILED"
    if record.get("status") != "current":
        return "SOURCE UNAVAILABLE"
    return "ARCHIVED PUBLIC SOURCE" if record.get("archived") else ""


def card(project, record, theme, mobile=False, rank=1, last=False, showcase=None):
    """Responsive visual walkthrough; never claims live system activity."""
    p, width = palette(theme), 360 if mobile else 900
    steps = (showcase or {}).get("steps") or [
        {"label": "Explore", "text": project["summary"]},
        {"label": "Inspect", "text": " · ".join(project["stack"]) or "Public source"},
        {"label": "Build", "text": "Read the source and project documentation."}]
    height = 626 if mobile else 332
    body = motion_style() + f'<rect width="{width}" height="{height}" rx="14" fill="{p["bg"]}"/>'
    body += text(24 if mobile else 32, 40, f"{rank:02}", 24, p["accent"], 600)
    body += text(70 if mobile else 80, 40, "HOW IT WORKS", 21, p["muted"], 600)
    for i, item in enumerate(steps):
        bx, by, bw, bh = (24, 67 + i * 170, 312, 144) if mobile else (32 + i * 290, 75, 256, 185)
        if i < 2:
            path = f'M180 {by + bh} V{by + 170}' if mobile else f'M{bx + bw} 168 H{bx + 290}'
            body += f'<path class="trace" d="{path}" stroke="{p["accent"]}" stroke-width="2"/>'
        body += f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="12" fill="{p["panel"]}" stroke="{p["border"]}"/>'
        color = p["violet"] if i == 1 else p["accent"]
        body += f'<circle cx="{bx + 30}" cy="{by + 30}" r="6" fill="{color}"/>'
        body += text(bx + 49, by + 39, item["label"], 27, p["ink"], 600)
        lines = textwrap.wrap(item["text"], 26 if mobile else 21)
        # The native walkthrough retains the full text; the graphic is a synopsis.
        if len(lines) > 3:
            lines = lines[:3]
            lines[-1] = lines[-1].rstrip('.,;') + "…"
        for j, line in enumerate(lines):
            body += text(bx + 20, by + 76 + j * 27, line, 21, p["muted"])
    notice = source_notice(record)
    footer = notice or "SOURCE-BASED WALKTHROUGH"
    for i, line in enumerate(textwrap.wrap(footer, 27 if mobile else 70)):
        body += text(24 if mobile else 32, height - 39 + i * 24, line, 21, p["accent"] if notice else p["muted"])
    return svg(width, height, project["name"], project["summary"] + (" " + notice if notice else ""), body, theme)


def picture(name, alt, width="900"):
    escaped = html.escape(alt, quote=True)
    return f'<picture>\n  <source media="(max-width: 900px) and (prefers-color-scheme: dark)" srcset="assets/portfolio/{name}-mobile-dark.svg">\n  <source media="(max-width: 900px) and (prefers-color-scheme: light)" srcset="assets/portfolio/{name}-mobile-light.svg">\n  <source media="(prefers-color-scheme: dark)" srcset="assets/portfolio/{name}-dark.svg">\n  <source media="(prefers-color-scheme: light)" srcset="assets/portfolio/{name}-light.svg">\n  <img src="assets/portfolio/{name}-light.svg" width="{width}" alt="{escaped}">\n</picture>'


def link(url, label):
    return f'<a href="{html.escape(safe_url(url), quote=True)}">{html.escape(label).replace("|", "&#124;")}</a>'


def validate_showcases(data, registry):
    """Curated public presentation metadata; never a remote-data input."""
    if set(data) != {"version", "projects"} or data["version"] != 1 or not isinstance(data["projects"], dict):
        raise ValueError("Invalid showcase configuration")
    public_ids = {q["id"] for q in registry["projects"] if q["visibility"] == "public"}
    for pid, item in data["projects"].items():
        if pid not in public_ids or set(item) - {"image", "mobile_image", "image_width", "caption", "steps", "sources"} or not {"image", "caption", "steps", "sources"} <= set(item):
            raise ValueError("Showcases require a registered public project")
        if type(item.get("image_width", 900)) is not int or not 320 <= item.get("image_width", 900) <= 900:
            raise ValueError("Invalid preview width")
        if item["image"] is not None and item["image"] != pid + ".png":
            raise ValueError("Showcase images must use the project ID")
        if "mobile_image" in item and (not item["image"] or item["mobile_image"] != pid + "-mobile.png"):
            raise ValueError("Mobile previews must use the project ID")
        if not item["caption"] or item["caption"] != plain(item["caption"], 180):
            raise ValueError("Invalid showcase caption")
        if not isinstance(item["steps"], list) or len(item["steps"]) != 3:
            raise ValueError("Showcases need three concise steps")
        for step in item["steps"]:
            if set(step) != {"label", "text"}:
                raise ValueError("Invalid walkthrough step")
            for key, limit in (("label", 20), ("text", 180)):
                if not step[key] or step[key] != plain(step[key], limit):
                    raise ValueError("Invalid walkthrough text")
        if not isinstance(item["sources"], list) or not 1 <= len(item["sources"]) <= 3:
            raise ValueError("Showcases need source references")
        for source in item["sources"]:
            if set(source) != {"label", "path"} or not source["label"] or source["label"] != plain(source["label"], 50):
                raise ValueError("Invalid showcase reference")
            path = source["path"]
            if not isinstance(path, str) or not re.fullmatch(r"blob/[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+", path) or any(part in {"", ".", ".."} for part in path.split("/")):
                raise ValueError("References must stay inside the public repository")
    return data["projects"]


def render(registry, snapshot, readme, showcases=None):
    validate_registry(registry)
    validate_snapshot(registry, snapshot)
    showcases = validate_showcases(showcases, registry) if showcases is not None else {}
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
            showcase = showcases.get(project["id"]) if record.get("url") else None
            outputs[f'assets/portfolio/{project["id"]}-{theme}.svg'] = card(project, record, theme, rank=rank, showcase=showcase)
            outputs[f'assets/portfolio/{project["id"]}-mobile-{theme}.svg'] = card(project, record, theme, mobile=True, rank=rank, showcase=showcase)
    current = sum(r["status"] == "current" for r in snapshot["projects"].values())
    public_count = len([p for p in projects if p["visibility"] == "public"])
    check = snapshot["checked_on"] or "not yet checked"
    parts = ['## Selected Work', '', 'Open a project or expand its walkthrough · **أعمال مختارة**', ''] if featured else []
    for rank, project in enumerate(featured, 1):
        record = snapshot["projects"].get(project["id"], {})
        showcase = showcases.get(project["id"], {}) if record.get("url") else {}
        notice = source_notice(record)
        title = link(record["url"], project["name"]) if record.get("url") else html.escape(project["name"])
        parts += [f'### {rank:02} · {title}', '']
        alt = project["name"] + ": " + project["summary"] + (" " + notice if notice else "")
        walkthrough = picture(project["id"], alt)
        image = (f'<img src="assets/showcases/{showcase["image"]}" width="{showcase.get("image_width", 900)}" alt="{html.escape(showcase["caption"], quote=True)}">'
                 if showcase.get("image") else walkthrough)
        if showcase.get("mobile_image"):
            image = f'<picture>\n<source media="(max-width: 900px)" srcset="assets/showcases/{showcase["mobile_image"]}">\n{image}\n</picture>'
        if record.get("url"):
            image = f'<a href="{html.escape(record["url"], quote=True)}">{image}</a>'
        # Keep linked responsive pictures inside a block so GitHub retains their sources.
        parts += ['<p align="center">', image, '</p>', '']
        if notice:
            parts += [f'<p><em>{html.escape(notice)}</em></p>', '']
        if showcase:
            parts += [f'<p>{html.escape(project["summary"])}<br><sub>{html.escape(showcase["caption"])}</sub></p>', '',
                      '<details>', f'<summary><strong>Explore {html.escape(project["name"])} · عرض التفاصيل</strong></summary>', '']
            if showcase.get("image"):
                parts += ['<p>', walkthrough, '</p>', '',
                          f'<a href="assets/showcases/{showcase["image"]}">View full preview</a>', '']
            for i, step in enumerate(showcase["steps"], 1):
                parts += [f'{i}. **{html.escape(step["label"])}** — {html.escape(step["text"])}']
            parts += ['', f'<p><strong>Built with</strong> · {html.escape(" · ".join(project["stack"]))}</p>', '']
            parts += [' · '.join(link(record["url"] + '/' + source["path"], source["label"]) for source in showcase["sources"]), '', '</details>', '']
        else:
            parts += [html.escape(project["summary"]), '']
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
            if any(el.tag.rsplit("}", 1)[-1] not in {"svg", "title", "desc", "rect", "circle", "path", "text", "g", "defs", "linearGradient", "radialGradient", "stop", "style"} for el in root.iter()):
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
        showcase_file = root / "portfolio/showcases.json"
        showcases = json.loads(showcase_file.read_text(encoding="utf-8")) if showcase_file.exists() else None
        if showcases is not None:
            for item in validate_showcases(showcases, registry).values():
                for image_name in (item["image"], item.get("mobile_image")):
                    if not image_name:
                        continue
                    image_path = root / "assets/showcases" / image_name
                    if image_path.is_symlink() or not image_path.is_file():
                        raise ValueError("Missing or unsafe showcase image")
        outputs = render(registry, snapshot, (root / "README.md").read_text(encoding="utf-8"), showcases)
        write_outputs(root, outputs, args.check)
        states = [r["status"] for r in snapshot["projects"].values()]
        print(f'Portfolio {"verified" if args.check else "rendered"}: {len(outputs)} files; {states.count("current")} current, {states.count("stale")} cached, {states.count("unavailable")} unavailable sources.')
    except (ValueError, KeyError, TypeError, OSError, ET.ParseError):
        print("Portfolio validation failed. Check registry, snapshot and generated-block structure; no source payloads are logged.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
