import copy
from html.parser import HTMLParser
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("portfolio", ROOT / "tools/update_portfolio.py")
p = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p)


def full_registry():
    return json.loads((ROOT / "portfolio/projects.json").read_text(encoding="utf-8"))


def registry():
    result = full_registry()
    result["projects"] = [result["projects"][0], result["projects"][-1]]
    return result


def empty():
    return {"version": 1, "checked_on": None, "discovery_status": "stale", "discovered": [], "projects": {}}


def metadata(owner, name, **extra):
    return {"name": name, "full_name": f"{owner}/{name}", "private": False, "fork": False, "archived": False, "topics": [], "description": "Public source", "html_url": f"https://github.com/{owner}/{name}", "language": "Python", "pushed_at": "2026-10-03T10:00:00Z", "default_branch": "main", **extra}


class FakeAPI:
    def __init__(self, config, discovery=None, metadata_error=None, optional_error=None, private=False, release=None, runs=None):
        self.config = config
        self.discovery = discovery or []
        self.metadata_error = metadata_error
        self.optional_error = optional_error
        self.private = private
        self.release = release
        self.runs = [] if runs is None else runs
        self.calls = []

    def get(self, path):
        self.calls.append(path)
        if path.startswith("/users/"):
            if isinstance(self.discovery, Exception):
                raise self.discovery
            return self.discovery
        if "/releases/latest" in path:
            if self.optional_error:
                raise self.optional_error
            if self.release:
                return self.release
            raise p.SourceError(404)
        if "/actions/runs?" in path:
            if self.optional_error:
                raise self.optional_error
            return {"workflow_runs": self.runs}
        if self.metadata_error:
            raise self.metadata_error
        name = path.rsplit("/", 1)[-1]
        return metadata(self.config["owner"], name, private=self.private)


class Markup(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.elements = []
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def attributes(self, tag):
        return [attrs for name, attrs in self.elements if name == tag]


class PortfolioTests(unittest.TestCase):
    def setUp(self):
        self.registry = registry()
        self.readme = "Handwritten before\n" + p.START + "\nold\n" + p.END + "\nHandwritten after\n"

    def current(self):
        return p.refresh(self.registry, empty(), FakeAPI(self.registry), "2026-10-09")

    def disclosure(self, readme, marker):
        sections = re.findall(r"<details\b[^>]*>.*?</details>", readme, flags=re.S | re.I)
        matches = [section for section in sections if marker in section]
        self.assertEqual(len(matches), 1, f"Expected one disclosure containing {marker!r}")
        self.assertNotIn("open", Markup(matches[0]).attributes("details")[0])
        return matches[0]

    def test_feature_order_public_index_and_private_disclosure(self):
        config = full_registry()
        snapshot = p.refresh(config, empty(), FakeAPI(config), "2026-10-09")
        readme = p.render(config, snapshot, self.readme)["README.md"]
        features, index = readme.split("More projects", 1)
        self.assertLess(features.index("Saudi Market Intelligence"), features.index("Bidaya"))
        self.assertNotIn("Data Science Basics", features)
        public_index = index.split("<details", 1)[0]
        others = [project for project in config["projects"] if project["visibility"] == "public" and not project["featured"]]
        self.assertEqual(others[0]["id"], "data-science-basics")
        positions = [public_index.index(project["name"]) for project in others]
        self.assertEqual(positions, sorted(positions))

        private = self.disclosure(readme, "CodeForge")
        evidence = self.disclosure(readme, "Sources, releases and workflow checks")
        self.assertNotEqual(private, evidence)
        for project in config["projects"]:
            if project["visibility"] == "summary-only":
                self.assertIn(project["name"], private)
                self.assertIn(project["summary"], private)
                self.assertNotIn(project["name"], readme.replace(private, ""))
        self.assertNotIn("https://", private)

    def test_feature_figures_and_bidaya_screenshot_have_text_and_sources(self):
        config = full_registry()
        snapshot = p.refresh(config, empty(), FakeAPI(config), "2026-10-09")
        outputs = p.render(config, snapshot, self.readme)
        readme = outputs["README.md"]
        tags = Markup(readme)
        linked_urls = {tag["href"] for tag in tags.attributes("a") if "href" in tag}
        showcases = json.loads((ROOT / "portfolio/showcases.json").read_text(encoding="utf-8"))["projects"]
        for project_id in ("saudi-market-intelligence", "bidaya"):
            prefix = f"assets/portfolio/{project_id}"
            sources = [tag for tag in tags.attributes("source") if tag["srcset"].startswith(prefix + "-")]
            self.assertEqual([tag["srcset"] for tag in sources[:3]], [
                prefix + "-mobile-dark.svg", prefix + "-mobile-light.svg", prefix + "-dark.svg"
            ])
            for tag in sources[:2]:
                self.assertIn("max-width:", tag["media"])
            for tag in sources:
                self.assertIn(tag["srcset"], outputs)
            fallback, = [tag for tag in tags.attributes("img") if tag["src"] == prefix + "-light.svg"]
            self.assertIn(fallback["src"], outputs)
            self.assertTrue(fallback.get("alt", "").strip())
            for source in showcases[project_id]["sources"]:
                url = snapshot["projects"][project_id]["url"] + "/" + source["path"]
                self.assertIn(url, linked_urls)
                self.assertIn(source["label"], readme)
                self.assertRegex(source["path"], r"^blob/[0-9a-f]{40}/")

        screenshot = self.disclosure(readme, "assets/showcases/bidaya.png")
        image, = [tag for tag in Markup(screenshot).attributes("img") if tag["src"] == "assets/showcases/bidaya.png"]
        self.assertTrue(image.get("alt", "").strip())
        self.assertTrue((ROOT / image["src"]).read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))

    def test_promoting_a_project_without_art_preserves_native_summary_and_link(self):
        config = full_registry()
        project = next(project for project in config["projects"] if project["id"] == "mindmaps")
        project["featured"] = True
        snapshot = p.refresh(config, empty(), FakeAPI(config), "2026-10-09")
        outputs = p.render(config, snapshot, self.readme)
        feature, index = outputs["README.md"].split("More projects", 1)
        self.assertIn(project["summary"], feature)
        self.assertIn(snapshot["projects"][project["id"]]["url"], feature)
        self.assertNotIn(project["name"], index.split("</details>", 1)[0])
        self.assertFalse(any(name.startswith("assets/portfolio/mindmaps-") for name in outputs))

    def test_showcase_evidence_must_be_local_or_pinned_to_the_public_source(self):
        manifest = json.loads((ROOT / "portfolio/showcases.json").read_text(encoding="utf-8"))
        item = manifest["projects"]["bidaya"]
        for image in ("../bidaya.png", "https://example.org/figure.png"):
            with self.subTest(image=image), self.assertRaises(ValueError):
                item["image"] = image
                p.render(self.registry, self.current(), self.readme, manifest)
        item["image"] = "bidaya.png"
        for path in ("blob/main/README.md", "blob/" + "a" * 40 + "/../README.md"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                item["sources"][0]["path"] = path
                p.render(self.registry, self.current(), self.readme, manifest)

    def test_private_summary_never_requests_repository(self):
        config = full_registry()
        api = FakeAPI(config)
        result = p.refresh(config, empty(), api, "2026-10-09")
        public = [project for project in config["projects"] if project["visibility"] == "public"]
        queried = {path.split("/")[3] for path in api.calls if path.startswith("/repos/")}
        self.assertEqual(queried, {project["repository"] for project in public})
        self.assertEqual(set(result["projects"]), {project["id"] for project in public})

    def test_unchecked_source_has_no_repository_link(self):
        outputs = p.render(self.registry, empty(), self.readme)
        self.assertIn("unavailable", outputs["README.md"].lower())
        self.assertFalse(any(name.endswith(".svg") for name in outputs))
        self.assertNotIn(f'https://github.com/{self.registry["owner"]}/saudi-market-intelligence', outputs["README.md"])

    def test_private_summary_needs_publication_basis_and_cannot_have_repository(self):
        private = self.registry["projects"][-1]
        private["repository"] = "Stock_101"
        with self.assertRaises(ValueError):
            p.validate_registry(self.registry)
        del private["repository"]
        del private["publication_basis"]
        with self.assertRaises(ValueError):
            p.validate_registry(self.registry)

    def test_transient_failure_keeps_last_verified_date_and_marks_cached(self):
        previous = self.current()
        result = p.refresh(self.registry, previous, FakeAPI(self.registry, metadata_error=p.SourceError(503)), "2026-10-10")
        item = result["projects"]["saudi-market-intelligence"]
        self.assertEqual(item["status"], "stale")
        self.assertEqual(item["checked_on"], "2026-10-09")
        outputs = p.render(self.registry, result, self.readme)
        readme = outputs["README.md"]
        self.assertIn("cached", readme.lower())
        self.assertIn(item["url"], readme)
        self.assertIn(item["checked_on"], readme)
        self.assertEqual(previous, self.current())

    def test_visibility_or_access_loss_removes_cached_remote_fields(self):
        for status in (301, 302, 403, 404, 410):
            with self.subTest(status=status):
                result = p.refresh(self.registry, self.current(), FakeAPI(self.registry, metadata_error=p.SourceError(status)), "2026-10-10")
                self.assertEqual(result["projects"]["saudi-market-intelligence"], {"status": "unavailable", "checked_on": None})
                output = p.render(self.registry, result, self.readme)["README.md"]
                self.assertNotIn("https://github.com/Abdulrahman-S-Asiri/saudi-market-intelligence", output)

    def test_private_metadata_response_is_never_cached(self):
        result = p.refresh(self.registry, self.current(), FakeAPI(self.registry, private=True), "2026-10-10")
        self.assertEqual(result["projects"]["saudi-market-intelligence"]["status"], "unavailable")
        self.assertNotIn("language", result["projects"]["saudi-market-intelligence"])

    def test_bidaya_access_loss_hides_screenshot_figures_and_source_links(self):
        config = full_registry()
        config["projects"] = [project for project in config["projects"] if project["id"] == "bidaya"]
        previous = p.refresh(config, empty(), FakeAPI(config), "2026-10-09")
        result = p.refresh(config, previous, FakeAPI(config, private=True), "2026-10-10")
        outputs = p.render(config, result, self.readme)
        readme = outputs["README.md"]
        self.assertIn(config["projects"][0]["summary"], readme)
        self.assertNotIn(previous["projects"]["bidaya"]["url"], readme)
        self.assertNotIn("assets/showcases/", readme)
        self.assertFalse(any(name.endswith(".svg") for name in outputs))

    def test_archived_registered_project_stays_linked_and_labelled(self):
        snapshot = self.current()
        record = snapshot["projects"]["saudi-market-intelligence"]
        record["archived"] = True
        readme = p.render(self.registry, snapshot, self.readme)["README.md"]
        feature = readme.split("<details", 1)[0]
        self.assertIn("archived", feature.lower())
        self.assertIn(record["url"], feature)

    def test_rate_limit_keeps_cached_public_record(self):
        result = p.refresh(self.registry, self.current(), FakeAPI(self.registry, metadata_error=p.SourceError(429)), "2026-10-10")
        self.assertEqual(result["projects"]["saudi-market-intelligence"]["status"], "stale")

    def test_no_releases_and_workflows_are_ordinary_empty_states(self):
        item = self.current()["projects"]["saudi-market-intelligence"]
        self.assertIsNone(item["release"])
        self.assertIsNone(item["workflow"])
        self.assertEqual(item["release_status"], "current")
        self.assertEqual(item["workflow_status"], "current")

    def test_optional_source_failures_are_visible_without_hiding_public_repository(self):
        result = p.refresh(self.registry, empty(), FakeAPI(self.registry, optional_error=p.SourceError(500)), "2026-10-09")
        item = result["projects"]["saudi-market-intelligence"]
        self.assertEqual(item["status"], "current")
        self.assertEqual(item["workflow_status"], "unavailable")
        self.assertEqual(item["release_status"], "unavailable")
        self.assertIn("Could not verify", p.render(self.registry, result, self.readme)["README.md"])

    def test_stable_release_and_named_workflow_preserve_source_evidence(self):
        base = f'https://github.com/{self.registry["owner"]}/saudi-market-intelligence'
        release = {"draft": False, "prerelease": False, "name": "Release | v1", "tag_name": "v1", "published_at": "2026-10-09T01:00:00+03:00", "html_url": base + "/releases/tag/v1"}
        runs = [{"name": "Tests | Linux", "conclusion": "failure", "updated_at": "2026-10-09T10:00:00Z", "html_url": base + "/actions/runs/123"}]
        result = p.refresh(self.registry, empty(), FakeAPI(self.registry, release=release, runs=runs), "2026-10-09")
        record = result["projects"]["saudi-market-intelligence"]
        self.assertEqual(record["release"]["published_on"], "2026-10-08")
        self.assertEqual(record["workflow"]["conclusion"], "failure")
        output = p.render(self.registry, result, self.readme)["README.md"]
        evidence = self.disclosure(output, runs[0]["html_url"])
        self.assertIn(release["html_url"], evidence)
        self.assertIn("Tests &#124; Linux", evidence)
        self.assertIn("failure", evidence)
        self.assertIn("Release &#124; v1", evidence)

    def test_malformed_optional_payloads_do_not_hide_verified_repository(self):
        api = FakeAPI(self.registry, release={"draft": False, "prerelease": False}, runs=[None])
        result = p.refresh(self.registry, empty(), api, "2026-10-09")
        record = result["projects"]["saudi-market-intelligence"]
        self.assertEqual(record["status"], "current")
        self.assertEqual(record["release_status"], "unavailable")
        self.assertEqual(record["workflow_status"], "unavailable")

    def test_malformed_discovery_is_reported_as_stale(self):
        result = p.refresh(self.registry, empty(), FakeAPI(self.registry, discovery=[None]), "2026-10-09")
        self.assertEqual(result["discovery_status"], "stale")
        self.assertEqual(result["projects"]["saudi-market-intelligence"]["status"], "current")

    def test_discovery_requires_explicit_public_topic_and_excludes_forks_archived_and_profile(self):
        owner = self.registry["owner"]
        topic = [self.registry["discovery_topic"]]
        repos = [metadata(owner, "new-repo", topics=topic), metadata(owner, "private-repo", topics=topic, private=True), metadata(owner, "fork-repo", topics=topic, fork=True), metadata(owner, "old-repo", topics=topic, archived=True), metadata(owner, owner, topics=topic), metadata(owner, "untagged")]
        result = p.refresh(self.registry, empty(), FakeAPI(self.registry, discovery=repos), "2026-10-09")
        self.assertEqual([q["repository"] for q in result["discovered"]], ["new-repo"])
        self.assertIn("auto-new-repo", result["projects"])

    def test_topic_discovery_and_removal_update_public_index(self):
        repo = metadata(self.registry["owner"], "new-repo", topics=[self.registry["discovery_topic"]])
        previous = p.refresh(self.registry, empty(), FakeAPI(self.registry, discovery=[repo]), "2026-10-09")
        result = p.refresh(self.registry, previous, FakeAPI(self.registry), "2026-10-10")
        self.assertEqual(result["discovered"], [])
        self.assertNotIn("auto-new-repo", result["projects"])
        before = p.render(self.registry, previous, self.readme)["README.md"]
        after = p.render(self.registry, result, self.readme)["README.md"]
        index = before.split("More projects", 1)[1].split("<details", 1)[0]
        self.assertIn("new-repo", index)
        self.assertIn(previous["projects"]["auto-new-repo"]["url"], index)
        self.assertNotIn("new-repo", after)

    def test_demoting_project_cleans_unused_figures_only(self):
        snapshot = self.current()
        featured = p.render(self.registry, snapshot, self.readme)
        self.registry["projects"][0]["featured"] = False
        indexed = p.render(self.registry, snapshot, self.readme)
        self.assertIn("Saudi Market Intelligence", indexed["README.md"].split("More projects", 1)[1])
        with tempfile.TemporaryDirectory(prefix=".portfolio-test-", dir=ROOT) as directory:
            root = Path(directory)
            p.write_outputs(root, featured)
            generated = root / "assets/portfolio"
            figures = list(generated.glob("*.svg"))
            self.assertTrue(figures)
            manual = root / "assets/showcases/manual.svg"
            manual.parent.mkdir()
            manual.write_text("keep me", encoding="utf-8")
            with self.assertRaises(ValueError):
                p.write_outputs(root, indexed, check=True)
            p.write_outputs(root, indexed)
            self.assertFalse(any(figure.exists() for figure in figures))
            self.assertEqual(manual.read_text(encoding="utf-8"), "keep me")
            p.write_outputs(root, indexed, check=True)

    def previously_discovered(self):
        repo = metadata(self.registry["owner"], "new-repo", topics=[self.registry["discovery_topic"]],
                        description="A previously public repository description.")
        return p.refresh(self.registry, empty(), FakeAPI(self.registry, discovery=[repo]), "2026-10-09")

    def test_failed_discovery_purges_candidate_that_individually_loses_access(self):
        previous = self.previously_discovered()
        api = FakeAPI(self.registry, discovery=p.SourceError(503), metadata_error=p.SourceError(404))
        result = p.refresh(self.registry, previous, api, "2026-10-10")
        self.assertIn(f'/repos/{self.registry["owner"]}/new-repo', api.calls)
        self.assertEqual(result["discovery_status"], "stale")
        self.assertEqual(result["discovered"], [])
        self.assertNotIn("auto-new-repo", result["projects"])
        self.assertEqual(result["projects"]["saudi-market-intelligence"], {"status": "unavailable", "checked_on": None})
        outputs = p.render(self.registry, result, self.readme)
        for path in ("README.md", "portfolio/snapshot.json"):
            self.assertNotIn("new-repo", outputs[path])
            self.assertNotIn(previous["discovered"][0]["summary"], outputs[path])

    def test_failed_discovery_retains_last_verified_candidate_during_transient_outage(self):
        previous = self.previously_discovered()
        api = FakeAPI(self.registry, discovery=p.SourceError(503), metadata_error=p.SourceError(503))
        result = p.refresh(self.registry, previous, api, "2026-10-10")
        self.assertEqual(result["discovery_status"], "stale")
        self.assertEqual(result["discovered"], previous["discovered"])
        record = result["projects"]["auto-new-repo"]
        self.assertEqual(record, {**previous["projects"]["auto-new-repo"], "status": "stale"})
        readme = p.render(self.registry, result, self.readme)["README.md"]
        index = self.disclosure(readme, "<summary>More projects</summary>")
        self.assertIn(record["url"], index)
        self.assertIn("cached", index.lower())
        self.assertIn("2026-10-09", index)

    def test_offline_render_scrubs_legacy_unavailable_discovery_without_mutating_input(self):
        legacy = self.previously_discovered()
        legacy["projects"]["auto-new-repo"] = {"status": "unavailable", "checked_on": None}
        original = copy.deepcopy(legacy)
        p.validate_snapshot(self.registry, legacy)
        outputs = p.render(self.registry, legacy, self.readme)
        self.assertEqual(legacy, original)
        cleaned = json.loads(outputs["portfolio/snapshot.json"])
        self.assertEqual(cleaned["discovered"], [])
        self.assertNotIn("auto-new-repo", cleaned["projects"])
        self.assertNotIn(legacy["discovered"][0]["summary"], outputs["README.md"])
        self.assertNotIn("new-repo", outputs["README.md"])
        self.assertEqual(outputs, p.render(self.registry, cleaned, outputs["README.md"]))

    def test_generation_is_deterministic_and_preserves_manual_copy(self):
        snapshot = self.current()
        outputs = p.render(self.registry, snapshot, self.readme)
        self.assertEqual(outputs, p.render(self.registry, snapshot, outputs["README.md"]))
        self.assertTrue(outputs["README.md"].startswith("Handwritten before\n"))
        self.assertTrue(outputs["README.md"].endswith("\nHandwritten after\n"))
        with tempfile.TemporaryDirectory(prefix=".portfolio-test-", dir=ROOT) as directory:
            root = Path(directory)
            p.write_outputs(root, outputs)
            p.write_outputs(root, outputs, check=True)
            (root / "README.md").write_text("tampered", encoding="utf-8")
            with self.assertRaises(ValueError):
                p.write_outputs(root, outputs, check=True)

    def test_broken_or_duplicate_generated_markers_stop_generation(self):
        for readme in ("no markers", p.END + p.START, self.readme + p.START, self.readme + p.END):
            with self.subTest(readme=readme), self.assertRaises(ValueError):
                p.render(self.registry, self.current(), readme)

    def test_remote_markup_is_escaped_and_svg_has_no_active_content(self):
        self.registry["projects"][0]["summary"] = '<script>alert("unsafe")</script> & a "quote"'
        outputs = p.render(self.registry, self.current(), self.readme)
        for name, content in outputs.items():
            if not name.endswith(".svg"):
                continue
            root = ET.fromstring(content)
            tags = {el.tag.rsplit("}", 1)[-1] for el in root.iter()}
            self.assertTrue({"title", "desc", "text"} <= tags)
            self.assertFalse({"script", "image", "foreignObject", "animate"} & tags)
        self.assertNotIn("<script>", outputs["README.md"])

    def test_malicious_links_paths_and_duplicate_ids_are_rejected(self):
        for url in ("javascript:alert(1)", "http://github.com/a", "https://user:password@github.com/a", "https://github.com/a\n"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                p.safe_url(url)
        self.registry["projects"][0]["id"] = "../../outside"
        with self.assertRaises(ValueError):
            p.validate_registry(self.registry)
        self.registry = registry()
        self.registry["projects"][-1]["id"] = self.registry["projects"][0]["id"]
        with self.assertRaises(ValueError):
            p.validate_registry(self.registry)

    def test_snapshot_rejects_private_unregistered_or_unavailable_metadata(self):
        snapshot = self.current()
        snapshot["projects"]["stock-101"] = copy.deepcopy(next(iter(snapshot["projects"].values())))
        with self.assertRaises(ValueError):
            p.validate_snapshot(self.registry, snapshot)
        snapshot = self.current()
        snapshot["projects"]["saudi-market-intelligence"]["status"] = "unavailable"
        with self.assertRaises(ValueError):
            p.validate_snapshot(self.registry, snapshot)

    def test_same_day_unchanged_refresh_does_not_create_output_changes(self):
        previous = self.current()
        repeated = p.refresh(self.registry, previous, FakeAPI(self.registry), "2026-10-09")
        self.assertEqual(previous, repeated)
        next_day = p.refresh(self.registry, previous, FakeAPI(self.registry), "2026-10-10")
        self.assertNotEqual(previous, next_day)

    def test_workflow_url_must_belong_to_verified_source(self):
        snapshot = self.current()
        snapshot["projects"]["saudi-market-intelligence"]["workflow"] = {"name": "Tests", "conclusion": "success", "completed_on": "2026-10-09", "url": "https://example.com/fake-evidence"}
        with self.assertRaises(ValueError):
            p.validate_snapshot(self.registry, snapshot)

    def test_http_client_bounds_retries_and_does_not_log_secret(self):
        with patch.dict("os.environ", {"GITHUB_TOKEN": "test-secret"}):
            client = p.GitHub()
        with patch.object(client.opener, "open", side_effect=URLError("network failure")) as opener, patch.object(p.time, "sleep"):
            with self.assertRaises(p.SourceError) as error:
                client.get("/repos/owner/repo")
            self.assertEqual(opener.call_count, 2)
            self.assertNotIn("test-secret", str(error.exception))
            request = opener.call_args.args[0]
            self.assertEqual(request.full_url, "https://api.github.com/repos/owner/repo")
        for path in ("https://evil.example", "/repos/../secret", "/user/installations"):
            with self.assertRaises(ValueError):
                client.get(path)

    def test_rate_limited_403_is_distinct_from_permission_denial(self):
        client = p.GitHub()
        for remaining, expected in (("0", 429), ("10", 403)):
            error = HTTPError("https://api.github.com", 403, "Forbidden", {"X-RateLimit-Remaining": remaining}, None)
            with patch.object(client.opener, "open", side_effect=error):
                with self.assertRaises(p.SourceError) as result:
                    client.get("/repos/owner/repo")
                self.assertEqual(result.exception.status, expected)

    def test_full_registry_and_real_checked_in_snapshot_validate(self):
        config = p.validate_registry(json.loads((ROOT / "portfolio/projects.json").read_text(encoding="utf-8")))
        snapshot_file = ROOT / "portfolio/snapshot.json"
        if snapshot_file.exists():
            p.validate_snapshot(config, json.loads(snapshot_file.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
