import copy
from datetime import date
import importlib.util
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


class PortfolioTests(unittest.TestCase):
    def setUp(self):
        self.registry = registry()
        self.readme = "Handwritten before\n" + p.START + "\nold\n" + p.END + "\nHandwritten after\n"

    def current(self):
        return p.refresh(self.registry, empty(), FakeAPI(self.registry), "2026-10-09")

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

    def test_topic_removal_removes_snapshot_and_generated_assets(self):
        repo = metadata(self.registry["owner"], "new-repo", topics=[self.registry["discovery_topic"]])
        previous = p.refresh(self.registry, empty(), FakeAPI(self.registry, discovery=[repo]), "2026-10-09")
        result = p.refresh(self.registry, previous, FakeAPI(self.registry), "2026-10-10")
        self.assertEqual(result["discovered"], [])
        self.assertNotIn("auto-new-repo", result["projects"])
        with tempfile.TemporaryDirectory(prefix=".portfolio-test-", dir=ROOT) as directory:
            root = Path(directory)
            p.write_outputs(root, p.render(self.registry, previous, self.readme))
            p.write_outputs(root, p.render(self.registry, result, self.readme))
            self.assertFalse((root / "assets/portfolio/auto-new-repo-dark.svg").exists())

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
