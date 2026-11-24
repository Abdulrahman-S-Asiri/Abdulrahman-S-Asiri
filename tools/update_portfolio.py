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
MODULE_ROOT = Path(__file__).resolve().parents[1]
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
    allowed = {"id", "name", "visibility", "repository", "category", "summary", "subtitle", "stack", "featured", "publication_basis"}
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
        if "subtitle" in project and (not project["subtitle"] or project["subtitle"] != plain(project["subtitle"], 80)):
            raise ValueError("The optional display subtitle must be short and single-line")
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
        if record.get("status") != "unavailable" and (set(record) != fields or not record.get("checked_on") or not record.get("url")):
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


INK_THEMES = {
    "light": {"paper": "#FAFBFE", "ink": "#111E42", "blue": "#234BBC", "wash": "#739ADC", "rule": "#647394"},
    "dark": {"paper": "#101A30", "ink": "#F1F5FF", "blue": "#9FBEFF", "wash": "#6E96EB", "rule": "#8599C3"},
}
FONT = "Segoe UI,Helvetica Neue,Arial,sans-serif"
ILLUSTRATIONS = {
    "saudi-market-intelligence": "Three stages connect a market database, an analysis engine and a research report.",
    "bidaya": "Overlapping Arabic and English speech bubbles illustrate bilingual interview practice.",
}


def validate_showcases(manifest):
    if set(manifest) != {"version", "projects"} or manifest["version"] != 1 or not isinstance(manifest["projects"], dict):
        raise ValueError("Invalid showcase manifest")
    for pid, item in manifest["projects"].items():
        if not SLUG.fullmatch(pid) or not isinstance(item, dict):
            raise ValueError("Invalid showcase")
        if set(item) - {"caption", "image", "mobile_image", "image_width", "sources", "steps"}:
            raise ValueError("Unknown showcase field")
        if not plain(item["caption"]) or item["caption"] != plain(item["caption"]):
            raise ValueError("Invalid showcase caption")
        for image in (item["image"], item.get("mobile_image")):
            if image is not None and (not isinstance(image, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}\.png", image)):
                raise ValueError("Showcase images must be local PNG filenames")
        if item["image"] and (type(item.get("image_width")) is not int or not 1 <= item["image_width"] <= 1200):
            raise ValueError("Invalid screenshot width")
        if not isinstance(item["sources"], list) or not item["sources"]:
            raise ValueError("Showcases require a pinned source")
        for source in item["sources"]:
            if set(source) != {"label", "path"} or not plain(source["label"], 80) or source["label"] != plain(source["label"], 80):
                raise ValueError("Invalid showcase source")
            path = source["path"]
            if not isinstance(path, str) or not re.fullmatch(r"blob/[a-f0-9]{40}/[A-Za-z0-9._/-]+", path) or any(part in {"", ".", ".."} for part in path.split("/")):
                raise ValueError("Showcase sources must name a file at a full Git commit")
        if not isinstance(item["steps"], list):
            raise ValueError("Invalid showcase steps")
        for step in item["steps"]:
            if set(step) != {"label", "text"} or any(not plain(v) or v != plain(v) for v in step.values()):
                raise ValueError("Invalid showcase step text")
    return manifest["projects"]


def svg_text(x, y, label, size, color, weight=400):
    return f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" font-weight="{weight}" fill="{color}">{html.escape(label)}</text>'


def market_art(colors):
    # Three original line drawings share a light, irregular wash, like ink on paper.
    wash = (
        '<path d="M8 19 Q11 12 19 15 L34 11 48 13 58 10 62 17 67 20 65 32 68 44 64 53 66 61 56 67 43 65 '
        '32 70 23 67 15 69 10 62 4 58 7 48 3 39 7 31 Z"/>'
        '<path d="M7 49 Q19 62 26 58 T43 61 L59 56 66 51 63 62 59 68 44 67 31 73 16 70 10 64 3 60 Z" opacity=".65"/>'
        '<path d="M9 22 L20 18 27 19 32 16 M11 62 L17 65 23 64 M46 67 L54 63 61 63" fill="none" stroke="currentColor" stroke-width=".9"/>'
    )
    washes = ''.join(f'<g transform="translate({x} {y})" fill="{colors["wash"]}" color="{colors["wash"]}" opacity=".32">{wash}</g>'
                     for x, y in ((0, 5), (83, 3), (166, 5)))
    icons = (
        # Cylindrical price store.
        '<path d="M17 31 C17 23 49 22 49 31 C49 39 17 39 17 31 Z M17 31 L17 56 C17 63 49 63 49 55 L49 31 '
        'M17 40 C20 47 46 47 49 40 M17 48 C20 55 46 55 49 48"/>'
        '<path d="M21 29 Q28 25 38 27 M44 44 l1 -1 M44 52 l1 -1" stroke-width="1"/>'
        # Gear with an open center.
        '<path d="M110 27 L115 25 116 19 123 19 125 25 130 27 135 24 140 30 136 35 138 40 144 42 '
        '143 49 137 50 134 55 136 61 130 65 125 61 120 62 117 68 110 66 110 60 104 57 '
        '99 59 95 53 99 48 99 42 93 39 95 32 102 32 106 28 105 24 Z"/>'
        '<circle cx="119" cy="43" r="9"/>'
        # Folded report sheet with a small research chart.
        '<path d="M184 24 L205 23 215 32 215 63 184 64 Z M205 23 L205 33 215 32 '
        'M190 34 l10 0 M190 40 l16 -1 M191 57 v-5 h3 v5 Z M198 57 v-9 h3 v9 Z M205 57 v-14 h3 v14 Z"/>'
        '<path d="M67 44 L87 43 M82 40 L88 43 82 47 M151 43 L172 44 M167 40 L173 44 167 48"/>'
    )
    return washes + f'<g fill="none" stroke="{colors["blue"]}" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round">{icons}</g>'


def bidaya_art(colors):
    shadows = (
        '<path d="M8 38 L14 32 26 34 33 24 44 25 57 17 70 20 85 14 100 19 112 16 123 24 138 24 151 30 '
        '165 27 181 32 194 32 200 40 213 45 212 52 225 60 222 71 207 75 195 74 186 83 171 79 159 86 '
        '143 81 131 86 116 79 100 83 88 76 76 78 66 72 54 73 48 67 34 68 26 61 14 61 11 52 5 48 Z"/>'
        '<path d="M12 44 L25 39 38 33 56 32 68 25 85 27 99 23 119 31 132 30 153 37 175 36 186 43 199 42 '
        '211 52 217 63 211 69 198 70 186 77 169 75 152 79 131 74 112 76 97 69 75 69 56 61 38 63 25 56 14 53 Z" opacity=".52"/>'
    )
    bubbles = (
        '<path d="M35 12 Q34 6 43 6 L124 7 Q133 7 133 14 L132 48 Q131 53 124 53 L58 54 42 65 44 54 '
        'Q34 54 34 46 Z"/>'
        '<path d="M105 44 Q105 36 115 36 L194 37 Q202 37 202 45 L202 72 Q201 80 193 80 L190 80 194 91 177 81 '
        '111 80 Q104 79 104 73 Z"/>'
    )
    rules = '<path d="M64 25 Q87 24 119 25 M45 34 L117 34 M45 42 L93 42 M115 61 L190 61 M115 69 L180 69"/>'
    return (f'<g fill="{colors["wash"]}" opacity=".30">{shadows}</g>'
            f'<g fill="{colors["paper"]}" stroke="{colors["blue"]}" stroke-width="1.7" stroke-linejoin="round">{bubbles}</g>'
            f'<g fill="none" stroke="{colors["blue"]}" stroke-width="1.2" stroke-linecap="round">{rules}</g>'
            + svg_text(44, 27, "AR", 13, colors["ink"], 700) + svg_text(177, 53, "EN", 13, colors["ink"], 700))


def project_row(project, theme, mobile):
    colors = INK_THEMES[theme]
    pid = project["id"]
    artwork = market_art(colors) if pid == "saudi-market-intelligence" else bidaya_art(colors)
    subtitle = project.get("subtitle", project["summary"])
    title_lines = textwrap.wrap(project["name"], width=26 if mobile else 34)
    subtitle_lines = textwrap.wrap(subtitle, width=35 if mobile else 51)
    width = 380 if mobile else 900
    x, title_y = (12, 118) if mobile else (283, 60)
    title_step, sub_step = (32, 23) if mobile else (40, 28)
    subtitle_y = title_y + (len(title_lines) - 1) * title_step + sub_step + 5
    height = subtitle_y + (len(subtitle_lines) - 1) * sub_step + (18 if mobile else 31)
    art_transform = "translate(7 0) scale(1.04)" if mobile else f"translate(8 {(height - 100) // 2}) scale(1.04)"
    title = html.escape(project["name"])
    description = html.escape(project["summary"] + " " + ILLUSTRATIONS[pid])
    pieces = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
              f'<title id="title">{title}</title><desc id="desc">{description}</desc>',
              f'<rect width="{width}" height="{height}" fill="{colors["paper"]}"/>',
              f'<g transform="{art_transform}">{artwork}</g>']
    if not mobile:
        pieces.append(f'<path d="M262 25 V{height - 25}" fill="none" stroke="{colors["rule"]}" stroke-width="1"/>')
    for i, line in enumerate(title_lines):
        pieces.append(svg_text(x, title_y + i * title_step, line, 29 if mobile else 36, colors["ink"], 700))
    for i, line in enumerate(subtitle_lines):
        pieces.append(svg_text(x, subtitle_y + i * sub_step, line, 20 if mobile else 23, colors["ink"]))
    pieces.append("</svg>\n")
    return "".join(pieces)


def picture(pid, alt):
    parts = ["<p><picture>"]
    for theme in ("dark", "light"):
        parts.append(f'  <source media="(max-width: 900px) and (prefers-color-scheme: {theme})" srcset="assets/portfolio/{pid}-mobile-{theme}.svg">')
    for theme in ("dark", "light"):
        parts.append(f'  <source media="(prefers-color-scheme: {theme})" srcset="assets/portfolio/{pid}-{theme}.svg">')
    parts += [f'  <img src="assets/portfolio/{pid}-light.svg" width="100%" alt="{html.escape(alt, quote=True)}">', "</picture></p>"]
    return "\n".join(parts)

def escape_text(value):
    return html.escape(value).replace("|", "&#124;")


def link(url, label):
    return f'<a href="{html.escape(safe_url(url), quote=True)}">{escape_text(label)}</a>'


def source_note(record):
    if not record or record["status"] == "unavailable":
        return "Source unavailable"
    notes = []
    if record["status"] == "stale":
        notes.append("Cached source last verified " + record["checked_on"])
    if record["archived"]:
        notes.append("Archived repository")
    return " · ".join(notes)


def showcase_links(record, showcase):
    if not showcase or not record.get("url"):
        return []
    return [link(record["url"] + "/" + ref["path"], ref["label"]) for ref in showcase["sources"]]


def featured_section(project, record, showcase, outputs):
    pid, name, summary = project["id"], project["name"], project["summary"]
    if not record.get("url"):
        return [f"<h3>{escape_text(name)}</h3>", "", f"<p>{escape_text(summary)}</p>", "", "<p>Source unavailable</p>", ""]
    if pid in ILLUSTRATIONS and showcase:
        for theme in INK_THEMES:
            for mobile in (False, True):
                suffix = "-mobile" if mobile else ""
                outputs[f"assets/portfolio/{pid}{suffix}-{theme}.svg"] = project_row(project, theme, mobile)
        parts = [picture(pid, f'{name}. {summary} {ILLUSTRATIONS[pid]}'), ""]
    else:
        parts = [f"<h3>{escape_text(name)}</h3>", "", f"<p>{escape_text(summary)}</p>", ""]
    evidence_links = showcase_links(record, showcase)
    actions = [link(record["url"], name + " repository")]
    if not showcase or not showcase.get("image"):
        actions.extend(evidence_links)
    note = source_note(record)
    if note:
        actions.append(note)
    parts += ["<p>" + " · ".join(actions) + "</p>", ""]
    if showcase and showcase.get("image"):
        caption = escape_text(showcase["caption"])
        image_name = showcase["image"]
        width = showcase["image_width"]
        image = f'<img src="assets/showcases/{image_name}" width="{width}" alt="{caption}">'
        if showcase.get("mobile_image"):
            mobile_name = showcase["mobile_image"]
            image = f'<picture><source media="(max-width: 900px)" srcset="assets/showcases/{mobile_name}">{image}</picture>'
        parts += ['<details>', f'<summary>{escape_text(name)} preview and sources</summary>', '',
                  f'<p>{escape_text(summary)}</p>', '',
                  f'<p>{image}</p>',
                  '', f'<p><em>{caption}</em></p>', '',
                  '<p>' + ' · '.join(evidence_links) + '</p>', '', '</details>', '']
    return parts


def project_entry(project, record):
    label = link(record["url"], project["name"]) if record.get("url") else escape_text(project["name"])
    note = source_note(record)
    suffix = f" · {note}" if note else ""
    return f'<li><strong>{label}</strong> — {escape_text(project["summary"])}{suffix}</li>'


def source_entry(project, record):
    label = escape_text(project["name"])
    if not record.get("url"):
        return f'<li><strong>{label}</strong> · Source unavailable</li>'
    note = source_note(record)
    if record["status"] == "current":
        note = " · ".join(filter(None, ("Verified " + record["checked_on"], note)))
    release = record["release"]
    if release:
        release_text = link(release["url"], release["name"]) + " · " + release["published_on"]
    else:
        release_text = "Could not verify the latest stable release" if record["release_status"] == "unavailable" else "No stable release reported"
    workflow = record["workflow"]
    if workflow:
        workflow_text = link(workflow["url"], f'{workflow["name"]}: {workflow["conclusion"]}') + " · " + workflow["completed_on"]
    else:
        workflow_text = "Could not verify the latest workflow" if record["workflow_status"] == "unavailable" else "No completed workflow reported"
    return f'<li><strong>{label}</strong> · {note}<br>{release_text} · {workflow_text}</li>'


def render(registry, snapshot, readme, showcases=None):
    validate_registry(registry)
    validate_snapshot(registry, snapshot)
    if showcases is None:
        showcases = json.loads((MODULE_ROOT / "portfolio/showcases.json").read_text(encoding="utf-8"))
    cases = validate_showcases(showcases)
    if readme.count(START) != 1 or readme.count(END) != 1 or readme.index(START) >= readme.index(END):
        raise ValueError("README must contain one ordered generated block")
    records = snapshot["projects"]
    public = [p for p in registry["projects"] if p["visibility"] == "public"]
    # Only registry summaries have standing approval when a source loses visibility.
    public += [p for p in snapshot["discovered"] if records.get(p["id"], {}).get("url")]
    private = [p for p in registry["projects"] if p["visibility"] == "summary-only"]
    outputs = {}
    parts = ['<a name="selected-work"></a>', '', '## Selected work · أعمال مختارة', '']
    for project in public:
        if project["featured"]:
            parts.extend(featured_section(project, records.get(project["id"], {}), cases.get(project["id"]), outputs))
    others = [p for p in public if not p["featured"]]
    if others:
        parts += ['<details>', '<summary>More projects</summary>', '', '<ul>']
        parts += [project_entry(p, records.get(p["id"], {})) for p in others]
        parts += ['</ul>', '', '</details>', '']
    if private:
        parts += ['<details>', '<summary>Private work · Published summaries</summary>', '',
                  '<p>These descriptions were previously published on this profile.</p>', '', '<ul>']
        parts += [f'<li><strong>{escape_text(p["name"])}</strong> — {escape_text(p["summary"])}</li>' for p in private]
        parts += ['</ul>', '', '</details>', '']
    parts += ['<details>', '<summary>Sources, releases and workflow checks</summary>', '']
    if snapshot["checked_on"]:
        parts += [f'<p>Public sources checked on {snapshot["checked_on"]} UTC.</p>', '']
    if public:
        parts += ['<ul>']
        parts += [source_entry(p, records.get(p["id"], {})) for p in public]
        parts += ['</ul>', '']
    discovery = ("Public repository discovery used the last successful list." if snapshot["discovery_status"] == "stale"
                 else "Public repository discovery is current.")
    parts += [f'<p>{discovery} Included repositories can opt in with the <code>{registry["discovery_topic"]}</code> topic.</p>',
              '', '</details>']
    before, remainder = readme.split(START)
    _, after = remainder.split(END)
    outputs["README.md"] = before + START + "\n\n" + "\n".join(parts) + "\n\n" + END + after
    outputs["portfolio/snapshot.json"] = json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    for path, content in outputs.items():
        if path.endswith(".svg"):
            document = ET.fromstring(content)
            if any(el.tag.rsplit("}", 1)[-1] not in {"svg", "title", "desc", "rect", "circle", "path", "text", "g"} for el in document.iter()):
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
