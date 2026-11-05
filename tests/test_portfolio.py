import base64
import copy
from datetime import date
import importlib.util
from html.parser import HTMLParser
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("portfolio", ROOT / "tools/update_portfolio.py")
p = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p)


def registry():
    result = json.loads((ROOT / "portfolio/projects.json").read_text(encoding="utf-8"))
    result["projects"] = [next(q for q in result["projects"] if q["id"] == pid)
                          for pid in ("saudi-market-intelligence", "stock-101")]
    return result


def empty():
    return {"version": 1, "checked_on": None, "discovery_status": "stale", "discovered": [], "projects": {}}


class RenderedSections(HTMLParser):
    """Observe the public image list and content hidden in native disclosures."""
    def __init__(self, markup):
        super().__init__()
        self.depth = 0
        self.visible_images = []
        self.hidden_images = []
        self.hidden_text = []
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        if tag == "details":
            self.depth += 1
        if tag == "img":
            (self.hidden_images if self.depth else self.visible_images).append(dict(attrs)["src"])

    def handle_endtag(self, tag):
        if tag == "details":
            self.depth -= 1

    def handle_data(self, data):
        if self.depth:
            self.hidden_text.append(data)


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


class PortfolioTests(unittest.TestCase):
    def setUp(self):
        self.registry = registry()
        self.readme = "Handwritten before\n" + p.START + "\nold\n" + p.END + "\nHandwritten after\n"

    def current(self):
        return p.refresh(self.registry, empty(), FakeAPI(self.registry), "2026-10-09")

    def showcase(self):
        return {"version": 1, "projects": {"saudi-market-intelligence": {
            "image": "saudi-market-intelligence.png", "caption": "Example preview",
            "steps": [{"label": label, "text": "Public project explanation."}
                      for label in ("Collect", "Analyze", "Inspect")],
            "sources": [{"label": "Read source", "path": "blob/main/README.md"}]
        }}}

    def test_showcase_disclosure_preserves_preview_flow_stack_and_source(self):
        output = p.render(self.registry, self.current(), self.readme, self.showcase())["README.md"]
        sections = RenderedSections(output)
        self.assertEqual(sections.visible_images, ["assets/portfolio/saudi-market-intelligence-light.svg"])
        self.assertEqual(sections.hidden_images, ["assets/showcases/saudi-market-intelligence.png"])
        self.assertIn("Explore Saudi Market Intelligence", "".join(sections.hidden_text))
        self.assertIn("Python · PyTorch · FastAPI · React", "".join(sections.hidden_text))
        self.assertIn("https://github.com/Abdulrahman-S-Asiri/saudi-market-intelligence/blob/main/README.md", output)

    def test_access_loss_hides_showcase_media_and_deep_source_links(self):
        result = p.refresh(self.registry, self.current(), FakeAPI(self.registry, metadata_error=p.SourceError(404)), "2026-10-10")
        output = p.render(self.registry, result, self.readme, self.showcase())["README.md"]
        self.assertNotIn("assets/showcases/", output)
        self.assertNotIn("blob/main/README.md", output)
        self.assertIn("SOURCE UNAVAILABLE", output)
        svg = p.render(self.registry, result, self.readme, self.showcase())["assets/portfolio/saudi-market-intelligence-dark.svg"]
        self.assertNotIn("<image", svg)

    def test_unchecked_project_has_warning_and_no_showcase_references(self):
        outputs = p.render(self.registry, empty(), self.readme, self.showcase())
        self.assertIn("SOURCE UNAVAILABLE", outputs["assets/portfolio/saudi-market-intelligence-dark.svg"])
        self.assertIn("SOURCE UNAVAILABLE", outputs["README.md"])
        self.assertNotIn("assets/showcases/", outputs["README.md"])
        self.assertNotIn("blob/main/README.md", outputs["README.md"])
        self.assertNotIn("<image", outputs["assets/portfolio/saudi-market-intelligence-dark.svg"])

    def test_showcase_paths_cannot_escape_assets_or_repository(self):
        for field, value in (("image", "../secret.png"), ("mobile_image", "../secret.png"), ("source", "blob/main/../../secret"),
                             ("source", "https://example.com/asset")):
            with self.subTest(value=value):
                config = self.showcase()
                item = config["projects"]["saudi-market-intelligence"]
                if field in {"image", "mobile_image"}:
                    item[field] = value
                else:
                    item["sources"][0]["path"] = value
                with self.assertRaises(ValueError):
                    p.render(self.registry, self.current(), self.readme, config)

    def test_mobile_showcase_keeps_responsive_source_inside_linked_picture(self):
        config = self.showcase()
        config["projects"]["saudi-market-intelligence"]["mobile_image"] = "saudi-market-intelligence-mobile.png"
        output = p.render(self.registry, self.current(), self.readme, config)["README.md"]
        self.assertIn('media="(max-width: 900px)" srcset="assets/showcases/saudi-market-intelligence-mobile.png"', output)
        self.assertIn('<a href="https://github.com/Abdulrahman-S-Asiri/saudi-market-intelligence"><picture>', output)

    def test_only_first_three_featured_projects_get_images_in_registry_order(self):
        original = self.registry["projects"][0]
        self.registry["projects"] = [dict(original, id=pid, repository=pid, name=pid, featured=featured)
                                    for pid, featured in (("ordinary", False), ("first", True),
                                                          ("second", True), ("third", True), ("overflow", True))]
        outputs = p.render(self.registry, self.current(), self.readme)
        sections = RenderedSections(outputs["README.md"])
        self.assertEqual(sections.visible_images, ["assets/portfolio/first-light.svg",
                                                   "assets/portfolio/second-light.svg",
                                                   "assets/portfolio/third-light.svg"])
        self.assertEqual(sections.hidden_images, [])
        self.assertIn("ordinary", " ".join(sections.hidden_text))
        self.assertIn("overflow", " ".join(sections.hidden_text))
        self.assertIn("Python · PyTorch · FastAPI · React", "".join(sections.hidden_text))
        self.assertEqual(sum(name.endswith(".svg") for name in outputs), 30)
        for pid, number in (("first", "01"), ("second", "02"), ("third", "03")):
            for variant in ("dark", "light", "mobile-dark", "mobile-light"):
                root = ET.fromstring(outputs[f"assets/portfolio/{pid}-{variant}.svg"])
                labels = [el.text for el in root.findall("{http://www.w3.org/2000/svg}text")]
                self.assertIn(number, labels)

    def test_no_featured_projects_keeps_public_and_private_work_in_disclosures(self):
        self.registry["projects"][0]["featured"] = False
        outputs = p.render(self.registry, self.current(), self.readme)
        sections = RenderedSections(outputs["README.md"])
        self.assertEqual(sections.visible_images + sections.hidden_images, [])
        self.assertNotIn("## Selected", outputs["README.md"])
        self.assertIn("Saudi Market Intelligence", " ".join(sections.hidden_text))
        self.assertIn("Stock_101", " ".join(sections.hidden_text))
        self.assertEqual(sum(name.endswith(".svg") for name in outputs), 18)

    def test_discovered_projects_are_text_only_and_optional_evidence_is_collapsed(self):
        repo = metadata(self.registry["owner"], "new-repo", topics=[self.registry["discovery_topic"]])
        result = p.refresh(self.registry, empty(), FakeAPI(self.registry, discovery=[repo]), "2026-10-09")
        outputs = p.render(self.registry, result, self.readme)
        sections = RenderedSections(outputs["README.md"])
        self.assertEqual(sections.visible_images, ["assets/portfolio/saudi-market-intelligence-light.svg"])
        self.assertIn("new-repo", " ".join(sections.hidden_text))
        self.assertIn("No stable release reported", " ".join(sections.hidden_text))
        self.assertNotIn("auto-new-repo-dark.svg", " ".join(outputs))

    def test_secondary_unavailable_source_has_no_link_and_stale_status_is_visible(self):
        self.registry["projects"][0]["featured"] = False
        for status, label in ((503, "CACHED / SOURCE CHECK FAILED"), (404, "SOURCE UNAVAILABLE")):
            with self.subTest(status=status):
                result = p.refresh(self.registry, self.current(), FakeAPI(self.registry, metadata_error=p.SourceError(status)), "2026-10-10")
                output = p.render(self.registry, result, self.readme)["README.md"]
                self.assertIn(label, output)
                if status == 404:
                    self.assertNotIn("https://github.com/Abdulrahman-S-Asiri/saudi-market-intelligence", output)

    def test_private_summary_never_requests_repository(self):
        api = FakeAPI(self.registry)
        result = p.refresh(self.registry, empty(), api, "2026-10-09")
        self.assertEqual(set(result["projects"]), {"saudi-market-intelligence"})
        self.assertFalse(any("Stock_101" in call or "stock-101" in call for call in api.calls))

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
        self.assertIn("CACHED / SOURCE CHECK FAILED", outputs["assets/portfolio/saudi-market-intelligence-dark.svg"])
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
        self.assertIn("Tests &#124; Linux: failure", output)
        self.assertIn("Release &#124; v1", output)

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

    def test_topic_removal_removes_snapshot_and_public_listing(self):
        repo = metadata(self.registry["owner"], "new-repo", topics=[self.registry["discovery_topic"]])
        previous = p.refresh(self.registry, empty(), FakeAPI(self.registry, discovery=[repo]), "2026-10-09")
        result = p.refresh(self.registry, previous, FakeAPI(self.registry), "2026-10-10")
        self.assertEqual(result["discovered"], [])
        self.assertNotIn("auto-new-repo", result["projects"])
        self.assertIn("new-repo", p.render(self.registry, previous, self.readme)["README.md"])
        self.assertNotIn("new-repo", p.render(self.registry, result, self.readme)["README.md"])

    def test_unfeaturing_project_removes_previously_generated_images(self):
        with tempfile.TemporaryDirectory(prefix=".portfolio-test-", dir=ROOT) as directory:
            root = Path(directory)
            snapshot = self.current()
            p.write_outputs(root, p.render(self.registry, snapshot, self.readme))
            card_path = root / "assets/portfolio/saudi-market-intelligence-dark.svg"
            self.assertTrue(card_path.exists())
            self.registry["projects"][0]["featured"] = False
            p.write_outputs(root, p.render(self.registry, snapshot, self.readme))
            self.assertFalse(card_path.exists())
            self.assertTrue((root / "assets/portfolio/banner-dark.svg").exists())

    def test_failed_discovery_checks_previous_candidates_individually(self):
        repo = metadata(self.registry["owner"], "new-repo", topics=[self.registry["discovery_topic"]])
        previous = p.refresh(self.registry, empty(), FakeAPI(self.registry, discovery=[repo]), "2026-10-09")
        api = FakeAPI(self.registry, discovery=p.SourceError(503), metadata_error=p.SourceError(404))
        result = p.refresh(self.registry, previous, api, "2026-10-10")
        self.assertEqual(result["discovery_status"], "stale")
        self.assertEqual(result["projects"]["auto-new-repo"]["status"], "unavailable")

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
            self.assertTrue({"title", "desc"} <= tags)
            self.assertFalse({"script", "foreignObject", "animate"} & tags)
        self.assertNotIn("<script>", outputs["README.md"])

    def test_svg_validation_rejects_active_and_external_content(self):
        payloads = (
            '<image href="https://example.com/image.webp"/>',
            '<image href="data:image/svg+xml;base64,PHN2Zy8+"/>',
            '<image href="data:image/webp;base64,' + base64.b64encode(b"not a WebP").decode() + '"/>',
            '<script>alert(1)</script>',
            '<rect onload="alert(1)"/>',
            '<rect fill="url(https://example.com/a)"/>',
            '<style>@import "https://example.com/a";</style>',
            '<style>.x{fill:url(https://example.com/a)}</style>',
        )
        for body in payloads:
            with self.subTest(body=body), self.assertRaises(ValueError):
                p.validate_svg('<svg xmlns="http://www.w3.org/2000/svg">' + body + '</svg>')
        p.validate_svg('<svg xmlns="http://www.w3.org/2000/svg"><image href="' + p.artwork("blue-brain") + '"/></svg>')

    def test_artwork_allowlist_rejects_unknown_and_private_names(self):
        for name in ("../blue-brain", "stock-101", "private", "blue-artwork.webp"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                p.artwork(name)

    def test_real_assets_are_deterministic_self_contained_and_responsive(self):
        config = p.validate_registry(json.loads((ROOT / "portfolio/projects.json").read_text(encoding="utf-8")))
        snapshot = json.loads((ROOT / "portfolio/snapshot.json").read_text(encoding="utf-8"))
        outputs = p.render(config, snapshot, self.readme)
        self.assertEqual(outputs, p.render(config, snapshot, outputs["README.md"]))
        assets = {name: value for name, value in outputs.items() if name.endswith(".svg")}
        self.assertEqual(len(assets), 30)
        for name, content in assets.items():
            with self.subTest(name=name):
                p.validate_svg(content)
                root = ET.fromstring(content)
                self.assertEqual(root.get("width"), "320" if "-mobile-" in name else "900")
                for image in root.iter("{http://www.w3.org/2000/svg}image"):
                    self.assertTrue(image.get("href", "").startswith("data:image/webp;base64,"))
        banner = assets["assets/portfolio/banner-dark.svg"]
        self.assertIn("animation:turn 4.8s", banner)
        self.assertIn("animation:flow 1.6s linear 3", banner)
        self.assertIn("prefers-reduced-motion:reduce", banner)
        self.assertIn("animation:none", banner)
        for name in ("banner", "toolkit", "research", "connect"):
            self.assertNotEqual(assets[f"assets/portfolio/{name}-dark.svg"], assets[f"assets/portfolio/{name}-mobile-dark.svg"])

    def test_reduced_motion_selects_assets_without_animation(self):
        outputs = p.render(self.registry, self.current(), self.readme)
        for variant in ("banner-still", "banner-mobile-still"):
            content = outputs[f"assets/portfolio/{variant}.svg"]
            self.assertNotIn("@keyframes", content)
            self.assertNotIn("animation:", content)
            self.assertIn("Static neural scene", content)
        picture = p.picture("banner", "Profile")
        self.assertLess(picture.index("banner-mobile-still.svg"), picture.index("banner-still.svg"))
        self.assertLess(picture.index("banner-still.svg"), picture.index("banner-dark.svg"))
        actual = (ROOT / "README.md").read_text(encoding="utf-8").split("</picture>", 1)[0]
        self.assertIn('media="(prefers-reduced-motion: reduce)" srcset="assets/portfolio/banner-still.svg"', actual)

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
