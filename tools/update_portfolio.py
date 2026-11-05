#!/usr/bin/env python3
"""Refresh public GitHub metadata and render a reproducible profile. Stdlib only."""

import argparse
import base64
import functools
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
    # The approved artwork keeps its midnight canvas in both GitHub themes.
    return {"bg": "#001522", "ink": "#F3F8FF", "muted": "#C5DFEF", "accent": "#58EAFA"}


def svg(width, height, title, description, body, theme):
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc"><title id="title">{html.escape(title)}</title><desc id="desc">{html.escape(description)}</desc>{body}</svg>\n'


@functools.lru_cache(maxsize=2)
def artwork(name):
    """Only two reviewed, local raster assets can enter a generated SVG."""
    if name not in {"blue-artwork", "blue-brain"}:
        raise ValueError("Unknown identity artwork")
    root = Path(__file__).resolve().parents[1]
    path = root / "assets/identity" / (name + ".webp")
    if path.is_symlink() or not path.resolve().is_relative_to((root / "assets/identity").resolve()):
        raise ValueError("Unsafe identity artwork")
    data = path.read_bytes()
    if len(data) > 1_000_000 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ValueError("Expected bounded WebP artwork")
    return "data:image/webp;base64," + base64.b64encode(data).decode("ascii")


def scene(crop, x, y, width, height, fit="xMidYMid slice"):
    """A clipped viewport of the approved art plate; prose is drawn separately."""
    view = " ".join(str(n) for n in crop)
    return (f'<svg x="{x}" y="{y}" width="{width}" height="{height}" viewBox="{view}" overflow="hidden" preserveAspectRatio="{fit}">'
            f'<image width="787" height="1997" href="{artwork("blue-artwork")}"/></svg>')


def motion_style(enabled=True):
    # Visible entrance motion ends within five seconds; copy never moves.
    if not enabled:
        return '<style>.flow{stroke-dasharray:2 42}</style>'
    return ('<style>@keyframes turn{0%{transform:perspective(800px) rotateY(-22deg) rotate(-5deg)}'
            '45%{transform:perspective(800px) rotateY(18deg) rotate(4deg)}100%{transform:none}}'
            '@keyframes flow{from{stroke-dashoffset:240}to{stroke-dashoffset:0}}'
            '.brain{transform-box:fill-box;transform-origin:center;animation:turn 4.8s ease-in-out 1}'
            '.flow{stroke-dasharray:2 42;animation:flow 1.6s linear 3}'
            '@media(prefers-reduced-motion:reduce){.brain,.flow{animation:none}}</style>')


def streams(width, y):
    result = ''
    for i in range(4):
        result += (f'<path class="flow" d="M-20 {y+i*7} C{width*.28} {y-80+i*12} '
                   f'{width*.55} {y+90-i*12} {width+20} {y-45+i*6}" fill="none" '
                   f'stroke="{("#58EAFA", "#27AFFF")[i%2]}" stroke-width="{2 if i%2 else 3}" opacity=".75"/>')
    return result


def banner(registry, snapshot, theme, mobile=False, animated=True):
    p, profile = palette(theme), registry["profile"]
    width, height = (320, 526) if mobile else (900, 350)
    body = motion_style(animated) + f'<rect width="{width}" height="{height}" fill="{p["bg"]}"/>'
    body += scene((0, 1745, 787, 252), 0, 0, width, height, fit="none")
    bx, by, size = (14, 253, 292) if mobile else (478, -9, 394)
    body += f'<image class="brain" x="{bx}" y="{by}" width="{size}" height="{size}" href="{artwork("blue-brain")}"/>'
    body += streams(width, height - 31)
    if not mobile:
        body += '<defs><linearGradient id="copy-veil"><stop stop-color="#001522" stop-opacity=".96"/><stop offset=".83" stop-color="#001522" stop-opacity=".96"/><stop offset="1" stop-color="#001522" stop-opacity="0"/></linearGradient></defs><rect width="482" height="350" fill="url(#copy-veil)"/>'
    x = 22 if mobile else 40
    body += text(x, 30 if mobile else 42, profile["direction"].upper(), 16 if mobile else 19, p["accent"], 600)
    given, _, family = profile["name"].partition(" ")
    y = 77 if mobile else 107
    for line in [given, family]:
        body += text(x, y, line, 36 if mobile else 48, p["ink"], 700)
        y += 44 if mobile else 54
    y += 1 if mobile else 3
    for line in textwrap.wrap(profile["role"], 25 if mobile else 45):
        body += text(x, y, line, 21 if mobile else 22, p["ink"], 600)
        y += 28
    if not mobile:
        y += 15
        for line in ("Building data systems, Arabic AI", "and tools for market research."):
            body += text(x, y, line, 21, p["muted"])
            y += 29
    body += text(x, y + 6, profile["location"], 20, p["accent"])
    motion_note = 'Motion stops after 4.8 seconds.' if animated else 'Static neural scene.'
    return svg(width, height, profile["name"], f'{profile["role"]}. {profile["direction"]}. {profile["location"]}. Luminous blue neural brain and flowing data. {motion_note}', body, theme)


def source_notice(record):
    if record.get("status") == "stale":
        return "CACHED / SOURCE CHECK FAILED"
    if record.get("status") != "current":
        return "SOURCE UNAVAILABLE"
    return "ARCHIVED PUBLIC SOURCE" if record.get("archived") else ""


def card(project, record, theme, mobile=False, rank=1, last=False, showcase=None):
    """Illustrated project covers; source previews live in native disclosures."""
    p = palette(theme)
    # Artwork mapping is intentional, never inferred from a private repository.
    crops = {"data-science-basics": ((0, 475, 787, 221), (348, 475, 439, 221)),
             "bidaya": ((0, 700, 787, 215), (0, 700, 418, 215)),
             "saudi-market-intelligence": ((0, 925, 787, 220), (340, 925, 447, 220))}
    illustrated = project["id"] in crops and bool(record.get("url"))
    right = project["id"] == "bidaya" and illustrated and not mobile
    width = 320 if mobile else 900
    title_lines = textwrap.wrap(project["name"], 21 if mobile else 24, break_on_hyphens=False)
    summary_lines = textwrap.wrap(project["summary"], 26 if mobile else 36)
    stack_lines = textwrap.wrap(" · ".join(project["stack"]), 27 if mobile else 48, break_on_hyphens=False)
    x, y = (22, 68) if mobile else (490 if right else 38, 57)
    copy = text(x, 31, f"{rank:02}", 20, p["accent"], 600)
    for line in title_lines:
        copy += text(x, y, line, 28 if mobile else 30, p["ink"], 700)
        y += 34
    y += 9
    for line in summary_lines:
        copy += text(x, y, line, 22, p["muted"])
        y += 29
    y += 5
    for line in stack_lines:
        copy += text(x, y, line, 20 if mobile else 18, p["accent"])
        y += 27
    notice = source_notice(record)
    if notice:
        y += 6
        for line in textwrap.wrap(notice, 26 if mobile else 35):
            copy += text(x, y, line, 18, p["muted"], 600)
            y += 24
    height = y + (169 if illustrated and mobile else 20)
    height = max(height, 230 if not mobile else 200)
    body = f'<rect width="{width}" height="{height}" fill="{p["bg"]}"/>'
    if illustrated:
        if mobile:
            body += scene(crops[project["id"]][1], 0, y+2, width, 158)
        else:
            body += scene(crops[project["id"]][0], 0, 0, width, height)
            # Keep copy contrast independent of the brightest decorative pixels.
            body += '<defs><linearGradient id="veil"><stop stop-color="#001522"/><stop offset=".88" stop-color="#001522"/><stop offset="1" stop-color="#001522" stop-opacity="0"/></linearGradient></defs>'
            if not right:
                body += f'<rect width="470" height="{height}" fill="url(#veil)"/>'
    body += copy
    body += f'<path d="M22 {height-1} H{width-22}" stroke="#13617D"/>'
    description = f'{project["name"]}. {project["summary"]} Technologies: {", ".join(project["stack"])}.'
    if illustrated:
        description += ' Conceptual illustration, not an application screenshot or performance chart.'
    return svg(width, height, project["name"], description + (" " + notice if notice else ""), body, theme)


def identity_section(name, theme, mobile=False):
    p, width = palette(theme), 320 if mobile else 900
    if name == "toolkit":
        height = 570 if mobile else 250
        body = f'<rect width="{width}" height="{height}" fill="{p["bg"]}"/>'
        groups = [("Data systems", "Python · SQL · PostgreSQL"),
                  ("Applied AI", "PyTorch · Transformers · RAG"),
                  ("Engineering", "FastAPI · React · Docker")]
        if mobile:
            for i, (label, stack) in enumerate(groups):
                yy = i*190
                body += scene((i*262, 1258, 262, 158), 90, yy, 140, 102)
                body += text(22, yy+136, label, 27, p["ink"], 600)
                for j, line in enumerate(textwrap.wrap(stack, 29)):
                    body += text(22, yy+166+j*23, line, 20, p["accent"])
        else:
            body += scene((0, 1245, 787, 177), 0, 0, width, 170)
            for i, (label, stack) in enumerate(groups):
                body += text(30+i*300, 207, label, 26, p["ink"], 600)
                body += text(30+i*300, 238, stack, 18, p["accent"])
        title, desc = "Engineering Toolkit", "Data systems: Python, SQL, PostgreSQL. Applied AI: PyTorch, Transformers, RAG. Engineering: FastAPI, React, Docker."
    elif name == "research":
        height = 185 if mobile else 165
        body = f'<rect width="{width}" height="{height}" fill="{p["bg"]}"/>'
        body += scene((280, 1508, 507, 226), 0 if mobile else 450, 0, width if mobile else 450, height, fit="xMidYMid meet")
        if not mobile:
            body += text(38, 86, "Language. Retrieval.", 30, p["ink"], 600)
            body += text(38, 127, "Evaluation.", 30, p["accent"], 600)
        title, desc = "Research & Community", "Luminous documents connected to an embedding network. Research links are provided below."
    elif name == "connect":
        height = 140
        body = scene((0, 1750, 787, 247), 0, 0, width, height)
        body += text(22 if mobile else 38, 44 if mobile else 65, "Let’s Connect", 30 if mobile else 40, p["ink"], 700)
        title, desc = "Let’s Connect", "Blue data waves. Contact links are provided below."
    else:
        raise ValueError("Unknown identity section")
    return svg(width, height, title, desc, body, theme)


def validate_svg(content):
    allowed = {"svg", "title", "desc", "rect", "circle", "path", "text", "g", "defs", "linearGradient", "radialGradient", "stop", "style", "image"}
    root = ET.fromstring(content)
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag not in allowed:
            raise ValueError("Unexpected SVG content")
        for key, value in el.attrib.items():
            key = key.rsplit("}", 1)[-1]
            if key.lower().startswith("on"):
                raise ValueError("Active SVG attribute")
            if key == "href":
                if tag != "image" or not re.fullmatch(r"data:image/webp;base64,[A-Za-z0-9+/=]+", value):
                    raise ValueError("External or active SVG resource")
                data = base64.b64decode(value.split(",", 1)[1], validate=True)
                if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
                    raise ValueError("Invalid embedded image")
            if "url(" in value and not re.fullmatch(r"url\(#[A-Za-z0-9_-]+\)", value):
                raise ValueError("External SVG paint")
        if tag == "style" and re.search(r"url\s*\(|@import", el.text or "", re.I):
            raise ValueError("External SVG stylesheet")


def picture(name, alt, width="900"):
    escaped = html.escape(alt, quote=True)
    still = ('  <source media="(max-width: 900px) and (prefers-reduced-motion: reduce)" srcset="assets/portfolio/banner-mobile-still.svg">\n'
             '  <source media="(prefers-reduced-motion: reduce)" srcset="assets/portfolio/banner-still.svg">\n') if name == "banner" else ''
    return f'<picture>\n{still}  <source media="(max-width: 900px) and (prefers-color-scheme: dark)" srcset="assets/portfolio/{name}-mobile-dark.svg">\n  <source media="(max-width: 900px) and (prefers-color-scheme: light)" srcset="assets/portfolio/{name}-mobile-light.svg">\n  <source media="(prefers-color-scheme: dark)" srcset="assets/portfolio/{name}-dark.svg">\n  <source media="(prefers-color-scheme: light)" srcset="assets/portfolio/{name}-light.svg">\n  <img src="assets/portfolio/{name}-light.svg" width="{width}" alt="{escaped}">\n</picture>'


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
    outputs = {"assets/portfolio/banner-still.svg": banner(registry, snapshot, "dark", animated=False),
               "assets/portfolio/banner-mobile-still.svg": banner(registry, snapshot, "dark", mobile=True, animated=False)}
    for theme in ("dark", "light"):
        for name in ("toolkit", "research", "connect"):
            outputs[f"assets/portfolio/{name}-{theme}.svg"] = identity_section(name, theme)
            outputs[f"assets/portfolio/{name}-mobile-{theme}.svg"] = identity_section(name, theme, mobile=True)
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
    parts = ['## Selected Work · أعمال مختارة', ''] if featured else []
    for rank, project in enumerate(featured, 1):
        record = snapshot["projects"].get(project["id"], {})
        showcase = showcases.get(project["id"], {}) if record.get("url") else {}
        notice = source_notice(record)
        alt = project["name"] + ": " + project["summary"] + " Technologies: " + ", ".join(project["stack"]) + (" " + notice if notice else "")
        cover = picture(project["id"], alt)
        if record.get("url"):
            cover = f'<a href="{html.escape(record["url"], quote=True)}">{cover}</a>'
        # Paragraph wrapper preserves linked pictures in GitHub's sanitizer.
        parts += ['<p>', cover, '</p>', '']
        if record.get("url") and not showcase:
            parts += [link(record["url"], "View " + project["name"] + " ↗"), '']
        if notice:
            parts += [f'<p><em>{html.escape(notice)}</em></p>', '']
        if showcase:
            parts += ['<details>', f'<summary><strong>Explore {html.escape(project["name"])}</strong> · {link(record["url"], "View project ↗")}</summary>', '',
                      html.escape(project["summary"]), '']
            for i, step in enumerate(showcase["steps"], 1):
                parts += [f'{i}. **{html.escape(step["label"])}** — {html.escape(step["text"])}']
            parts += ['', f'<p><strong>Built with</strong> · {html.escape(" · ".join(project["stack"]))}</p>', '']
            if showcase.get("image"):
                preview = f'<img src="assets/showcases/{showcase["image"]}" width="{showcase.get("image_width", 900)}" alt="{html.escape(showcase["caption"], quote=True)}">'
                if showcase.get("mobile_image"):
                    preview = f'<picture>\n<source media="(max-width: 900px)" srcset="assets/showcases/{showcase["mobile_image"]}">\n{preview}\n</picture>'
                parts += ['<p>', preview, '</p>', '', html.escape(showcase["caption"]), '']
            parts += [' · '.join(link(record["url"] + '/' + ref["path"], ref["label"]) for ref in showcase["sources"]), '', '</details>', '']
        else:
            # Preserve readable content even when an image or source is unavailable.
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
            validate_svg(content)
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
