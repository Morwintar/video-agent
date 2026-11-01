from __future__ import annotations

import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "dashboard"))

try:
    import fastapi  # noqa: F401
    HAS_FASTAPI = True
except ModuleNotFoundError:
    # The agent's standalone Python lacks the dashboard dependencies.
    # Stub the small response surface used by filesystem tests.
    HAS_FASTAPI = False

    class _TestRouter:
        def get(self, *_args, **_kwargs):
            return lambda handler: handler

        def post(self, *_args, **_kwargs):
            return lambda handler: handler

        def delete(self, *_args, **_kwargs):
            return lambda handler: handler

    class _TestHTTPException(Exception):
        def __init__(self, status_code: int, detail: str) -> None:
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    class _TestFileResponse:
        def __init__(self, path, *, media_type=None, filename=None, content_disposition_type="attachment", **_kwargs):
            self.path = Path(path)
            self.media_type = media_type
            self.headers = {"accept-ranges": "bytes"}
            if filename:
                self.headers["content-disposition"] = f'{content_disposition_type}; filename="{filename}"'

    fastapi_stub = types.ModuleType("fastapi")
    fastapi_stub.APIRouter = _TestRouter
    fastapi_stub.HTTPException = _TestHTTPException
    responses_stub = types.ModuleType("fastapi.responses")
    responses_stub.FileResponse = _TestFileResponse
    fastapi_stub.responses = responses_stub
    sys.modules["fastapi"] = fastapi_stub
    sys.modules["fastapi.responses"] = responses_stub

import plugin_api  # noqa: E402
from plugin_api import _scan_folder, list_videos  # noqa: E402


class ScanFolderTests(unittest.TestCase):
    def test_lists_sorted_regular_mp4_files_and_skips_other_entries(self) -> None:
        with tempfile.TemporaryDirectory(dir="/opt/data/cache/scratch") as temp_dir:
            root = Path(temp_dir)
            waiting = root / "Waiting"
            waiting.mkdir()
            (waiting / "z-last.MP4").write_bytes(b"1234")
            (waiting / "a-first.mp4").write_bytes(b"12")
            (waiting / "notes.txt").write_text("not a video", encoding="utf-8")
            nested = waiting / "nested"
            nested.mkdir()
            (nested / "hidden.mp4").write_bytes(b"nested")

            result = _scan_folder(root, "waiting", "Waiting")

        self.assertTrue(result["available"])
        self.assertEqual(result["count"], 3)
        self.assertEqual(
            [video["name"] for video in result["videos"]],
            ["a-first.mp4", "nested/hidden.mp4", "z-last.MP4"],
        )
        self.assertEqual([video["sizeBytes"] for video in result["videos"]], [2, 6, 4])
        self.assertNotIn("path", result)

    def test_does_not_follow_video_symlinks(self) -> None:
        with tempfile.TemporaryDirectory(dir="/opt/data/cache/scratch") as temp_dir:
            root = Path(temp_dir)
            waiting = root / "Waiting"
            waiting.mkdir()
            outside = root / "outside.mp4"
            outside.write_bytes(b"outside")
            outside_dir = root / "outside-dir"
            outside_dir.mkdir()
            (outside_dir / "escaped.mp4").write_bytes(b"outside")
            link = waiting / "linked.mp4"
            link_dir = waiting / "linked-dir"
            try:
                link.symlink_to(outside)
                link_dir.symlink_to(outside_dir, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"Symlinks are unavailable: {exc}")

            result = _scan_folder(root, "waiting", "Waiting")

        self.assertTrue(result["available"])
        self.assertEqual(result["videos"], [])

    def test_missing_folder_is_reported_without_leaking_paths(self) -> None:
        with tempfile.TemporaryDirectory(dir="/opt/data/cache/scratch") as temp_dir:
            result = _scan_folder(Path(temp_dir), "finish", "Finish")

        self.assertFalse(result["available"])
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["videos"], [])
        self.assertNotIn("path", result)

    def test_route_lists_only_waiting_and_finish_from_active_hermes_home(self) -> None:
        with tempfile.TemporaryDirectory(dir="/opt/data/cache/scratch") as temp_dir:
            home = Path(temp_dir)
            media_root = home / "inbox" / "stream-test"
            waiting = media_root / "Waiting"
            finish = media_root / "Finish"
            extra = media_root / "Unrelated"
            for directory in (waiting, finish, extra):
                directory.mkdir(parents=True)
            (waiting / "incoming.mp4").write_bytes(b"w")
            (waiting / "records").mkdir()
            (waiting / "records" / "local-recording.mp4").write_bytes(b"wn")
            (finish / "render.mp4").write_bytes(b"f")
            (finish / "subfolder").mkdir()
            (finish / "subfolder" / "ignored.mp4").write_bytes(b"f2")
            (extra / "not-listed.mp4").write_bytes(b"x")

            with patch.dict(os.environ, {"HERMES_HOME": str(home)}):
                payload = list_videos()

        self.assertTrue(payload["readOnly"])
        self.assertEqual([folder["label"] for folder in payload["folders"]], ["Waiting", "Finish"])
        self.assertEqual([folder["count"] for folder in payload["folders"]], [2, 1])
        self.assertEqual(
            [video["name"] for folder in payload["folders"] for video in folder["videos"]],
            ["incoming.mp4", "records/local-recording.mp4", "render.mp4"],
        )
        self.assertTrue(
            all("path" not in video for folder in payload["folders"] for video in folder["videos"])
        )


class NewFeatureTests(unittest.TestCase):
    def test_finish_video_resolver_is_confined_to_regular_finish_mp4s(self) -> None:
        with tempfile.TemporaryDirectory(dir="/opt/data/cache/scratch") as temp_dir:
            root = Path(temp_dir)
            finish = root / "Finish"
            waiting = root / "Waiting"
            finish.mkdir()
            waiting.mkdir()
            (finish / "final.mp4").write_bytes(b"video")
            (waiting / "incoming.mp4").write_bytes(b"waiting")
            outside = root / "outside.mp4"
            outside.write_bytes(b"outside")
            try:
                (finish / "linked.mp4").symlink_to(outside)
            except OSError as exc:
                self.skipTest(f"Symlinks are unavailable: {exc}")

            self.assertTrue(hasattr(plugin_api, "_resolve_finish_video"), "finish video resolver is missing")
            resolver = plugin_api._resolve_finish_video
            self.assertEqual(resolver(root, "final.mp4"), finish / "final.mp4")
            self.assertIsNone(resolver(root, "../outside.mp4"))
            self.assertIsNone(resolver(root, "incoming.mp4"))
            self.assertIsNone(resolver(root, "linked.mp4"))
            self.assertIsNone(resolver(root, "notes.txt"))

    def test_finish_video_route_supports_range_playback(self) -> None:
        if not HAS_FASTAPI:
            self.skipTest("FastAPI/Starlette is unavailable in this Python environment")
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory(dir="/opt/data/cache/scratch") as temp_dir:
            root = Path(temp_dir)
            finish = root / "Finish"
            finish.mkdir()
            (finish / "final.mp4").write_bytes(b"0123456789")
            self.assertTrue(hasattr(plugin_api, "get_finished_video"), "finish video endpoint is missing")
            app = FastAPI()
            app.include_router(plugin_api.router, prefix="/api/plugins/waiting-studio")
            with patch("plugin_api._media_root", return_value=root):
                with TestClient(app) as client:
                    response = client.get(
                        "/api/plugins/waiting-studio/videos/finish/final.mp4",
                        headers={"Range": "bytes=2-5"},
                    )

        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, b"2345")
        self.assertTrue(response.headers["content-type"].startswith("video/mp4"))
        self.assertEqual(response.headers["content-range"], "bytes 2-5/10")
        self.assertTrue(response.headers["content-disposition"].startswith("inline"))

    def test_custom_skill_creation_is_plugin_local_and_listed_without_paths(self) -> None:
        with tempfile.TemporaryDirectory(dir="/opt/data/cache/scratch") as temp_dir:
            root = Path(temp_dir)
            self.assertTrue(hasattr(plugin_api, "_create_custom_skill"), "custom skill writer is missing")
            self.assertTrue(hasattr(plugin_api, "_list_custom_skills"), "custom skill listing is missing")
            created = plugin_api._create_custom_skill(
                root,
                "clip-review",
                "Review finished gameplay clips before export.",
                "## Checklist\n- Verify the clip begins with the key moment.\n- Preserve the HUD.",
            )
            skill_file = root / "skills" / "clip-review" / "SKILL.md"
            self.assertTrue(skill_file.is_file())
            content = skill_file.read_text(encoding="utf-8")
            self.assertIn("name: clip-review", content)
            self.assertIn('description: "Review finished gameplay clips before export."', content)
            self.assertIn("## Checklist", content)
            self.assertTrue(created["requiresGatewayReload"])

            listing = plugin_api._list_custom_skills(root)
            self.assertEqual(listing["skills"], [{
                "name": "clip-review",
                "description": "Review finished gameplay clips before export.",
            }])
            self.assertNotIn("path", listing["skills"][0])

    def test_custom_skill_http_routes_create_and_list_without_overwriting(self) -> None:
        if not HAS_FASTAPI:
            self.skipTest("FastAPI/Starlette is unavailable in this Python environment")
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory(dir="/opt/data/cache/scratch") as temp_dir:
            plugin_root = Path(temp_dir)
            app = FastAPI()
            app.include_router(plugin_api.router, prefix="/api/plugins/waiting-studio")
            payload = {
                "name": "clip-review",
                "description": "Review clips before export.",
                "instructions": "## Checklist\n- Preserve the source.",
            }
            with patch("plugin_api._plugin_root", return_value=plugin_root):
                with TestClient(app) as client:
                    created = client.post("/api/plugins/waiting-studio/skills", json=payload)
                    listing = client.get("/api/plugins/waiting-studio/skills")
                    duplicate = client.post("/api/plugins/waiting-studio/skills", json=payload)
                    invalid = client.post(
                        "/api/plugins/waiting-studio/skills",
                        json={**payload, "name": "../escape"},
                    )

        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["skill"]["name"], "clip-review")
        self.assertTrue(created.json()["requiresGatewayReload"])
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["skills"][0]["name"], "clip-review")
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(invalid.status_code, 422)
        self.assertFalse((plugin_root.parent / "escape" / "SKILL.md").exists())

    def test_generated_skill_is_discovered_by_portable_agent_plugin_loader(self) -> None:
        try:
            from hermes_cli.agent_plugins import load_agent_plugin
        except ImportError:
            self.skipTest("Hermes Agent Plugin loader is unavailable in this Python environment")

        with tempfile.TemporaryDirectory(dir="/opt/data/cache/scratch") as temp_dir:
            plugin_root = Path(temp_dir)
            shutil.copyfile(PLUGIN_ROOT / "plugin.json", plugin_root / "plugin.json")
            plugin_api._create_custom_skill(
                plugin_root,
                "clip-review",
                "Review finished clips before export.",
                "## Checklist\n- Preserve the source video.",
            )
            package = load_agent_plugin(plugin_root, plugin_root / "data")

        self.assertEqual(package.name, "waiting-studio")
        self.assertEqual([skill.name for skill in package.skills], ["clip-review"])
        self.assertEqual(package.diagnostics, ())

    def test_custom_skill_creation_rejects_traversal_and_existing_names(self) -> None:
        with tempfile.TemporaryDirectory(dir="/opt/data/cache/scratch") as temp_dir:
            root = Path(temp_dir)
            self.assertTrue(hasattr(plugin_api, "_create_custom_skill"), "custom skill writer is missing")
            with self.assertRaises(ValueError):
                plugin_api._create_custom_skill(root, "../outside", "Description.", "Instructions.")
            plugin_api._create_custom_skill(root, "valid-skill", "Description.", "Instructions.")
            with self.assertRaises(FileExistsError):
                plugin_api._create_custom_skill(root, "valid-skill", "Description.", "Replacement.")
            self.assertFalse((root.parent / "outside" / "SKILL.md").exists())

    def test_reference_url_classifies_approved_youtube_and_tiktok_hosts(self) -> None:
        self.assertEqual(
            plugin_api._validate_reference_url("https://youtu.be/abc123"),
            {"url": "https://youtu.be/abc123", "platform": "YouTube"},
        )
        self.assertEqual(
            plugin_api._validate_reference_url("https://www.youtube.com/shorts/abc123"),
            {"url": "https://www.youtube.com/shorts/abc123", "platform": "YouTube"},
        )
        self.assertEqual(
            plugin_api._validate_reference_url("https://vm.tiktok.com/ZMabc123/"),
            {"url": "https://vm.tiktok.com/ZMabc123/", "platform": "TikTok"},
        )

    def test_reference_url_rejects_host_spoofing_and_unsafe_urls(self) -> None:
        invalid_urls = [
            "https://youtube.com.evil.example/shorts/abc123",
            "https://youtu.be.evil.example/abc123",
            "https://evil.example/youtube.com/watch?v=abc123",
            "http://youtube.com/watch?v=abc123",
            "https://youtube.com:444/watch?v=abc123",
            "https://user@youtube.com/watch?v=abc123",
            "https://youtube.com/",
        ]
        for url in invalid_urls:
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    plugin_api._validate_reference_url(url)

    def test_reference_registry_persists_in_hermes_home_and_can_remove_entries(self) -> None:
        with tempfile.TemporaryDirectory(dir="/opt/data/cache/scratch") as temp_dir:
            home = Path(temp_dir)
            self.assertEqual(plugin_api._list_references(home), [])
            self.assertFalse((home / "state").exists(), "listing must not create persistent state")

            reference = plugin_api._add_reference(
                home,
                "https://www.youtube.com/shorts/abc123",
                "Observe le rythme du montage.",
            )
            store = home / "state" / "waiting-studio" / "references.json"
            self.assertEqual(store.stat().st_mode & 0o777, 0o600)
            self.assertEqual(plugin_api._list_references(home), [reference])
            self.assertTrue(plugin_api._delete_reference(home, reference["id"]))
            self.assertEqual(plugin_api._list_references(home), [])

    def test_new_references_capture_the_default_observation_checklist(self) -> None:
        with tempfile.TemporaryDirectory(dir="/opt/data/cache/scratch") as temp_dir:
            reference = plugin_api._add_reference(
                Path(temp_dir),
                "https://youtu.be/abc123",
                "Regarder l'accroche et les transitions.",
            )

        self.assertEqual(reference["note"], "Regarder l'accroche et les transitions.")
        self.assertGreaterEqual(len(reference["observationChecklist"]), 5)
        labels = [item["label"] for item in reference["observationChecklist"]]
        self.assertTrue(any("accroche" in label.casefold() for label in labels))
        self.assertTrue(any("sous-titres" in label.casefold() for label in labels))

    def test_nested_waiting_content_routes_accept_relative_video_paths(self) -> None:
        if not HAS_FASTAPI:
            self.skipTest("FastAPI/Starlette is unavailable in this Python environment")
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory(dir="/opt/data/cache/scratch") as temp_dir:
            root = Path(temp_dir)
            home = root / "home"
            home.mkdir()
            media_root = root / "media"
            waiting = media_root / "Waiting" / "records"
            waiting.mkdir(parents=True)
            (media_root / "Finish").mkdir()
            (waiting / "nested-recording.mp4").write_bytes(b"video")
            app = FastAPI()
            app.include_router(plugin_api.router, prefix="/api/plugins/waiting-studio")
            with patch("plugin_api._media_root", return_value=media_root), patch(
                "plugin_api._references_home", return_value=home
            ):
                with TestClient(app) as client:
                    workspace = client.get("/api/plugins/waiting-studio/content/records%2Fnested-recording.mp4")
                    moment = client.post(
                        "/api/plugins/waiting-studio/content/records%2Fnested-recording.mp4/moments",
                        json={"timeSeconds": 12.5, "note": "Moment fort", "targetFormat": "short"},
                    )
                    draft = client.post(
                        "/api/plugins/waiting-studio/content/records%2Fnested-recording.mp4/items",
                        json={"format": "short", "title": "Le duel", "concept": "Retour spectaculaire"},
                    )

        self.assertEqual(workspace.status_code, 200)
        self.assertEqual(workspace.json()["sourceFilename"], "records/nested-recording.mp4")
        self.assertEqual(moment.status_code, 201)
        self.assertEqual(moment.json()["moment"]["sourceFilename"], "records/nested-recording.mp4")
        self.assertEqual(draft.status_code, 201)
        self.assertEqual(draft.json()["item"]["sourceFilename"], "records/nested-recording.mp4")

    def test_waiting_video_resolver_accepts_nested_regular_mp4s_but_rejects_symlinks(self) -> None:
        with tempfile.TemporaryDirectory(dir="/opt/data/cache/scratch") as temp_dir:
            root = Path(temp_dir)
            waiting = root / "Waiting"
            finish = root / "Finish"
            waiting.mkdir()
            finish.mkdir()
            source = waiting / "source.mp4"
            source.write_bytes(b"original")
            nested = waiting / "records"
            nested.mkdir()
            nested_source = nested / "nested.mp4"
            nested_source.write_bytes(b"nested")
            (finish / "finished.mp4").write_bytes(b"finished")
            outside = root / "outside.mp4"
            outside.write_bytes(b"outside")
            outside_dir = root / "outside-dir"
            outside_dir.mkdir()
            (outside_dir / "escape.mp4").write_bytes(b"outside")
            try:
                (waiting / "linked.mp4").symlink_to(outside)
                (waiting / "linked-dir").symlink_to(outside_dir, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"Symlinks are unavailable: {exc}")

            self.assertTrue(hasattr(plugin_api, "_resolve_waiting_video"), "Waiting source resolver is missing")
            resolver = plugin_api._resolve_waiting_video
            self.assertEqual(resolver(root, "source.mp4"), source)
            self.assertEqual(resolver(root, "records/nested.mp4"), nested_source)
            self.assertIsNone(resolver(root, "../outside.mp4"))
            self.assertIsNone(resolver(root, "finished.mp4"))
            self.assertIsNone(resolver(root, "linked.mp4"))
            self.assertIsNone(resolver(root, "linked-dir/escape.mp4"))
            self.assertIsNone(resolver(root, "notes.txt"))

    def test_waiting_video_route_supports_range_playback(self) -> None:
        if not HAS_FASTAPI:
            self.skipTest("FastAPI/Starlette is unavailable in this Python environment")
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory(dir="/opt/data/cache/scratch") as temp_dir:
            root = Path(temp_dir)
            waiting = root / "Waiting"
            waiting.mkdir()
            (root / "Finish").mkdir()
            (waiting / "source.mp4").write_bytes(b"0123456789")
            nested = waiting / "records"
            nested.mkdir()
            (nested / "record.mp4").write_bytes(b"abcdefghij")
            (root / "Finish" / "finished.mp4").write_bytes(b"finished")
            app = FastAPI()
            app.include_router(plugin_api.router, prefix="/api/plugins/waiting-studio")
            with patch("plugin_api._media_root", return_value=root):
                with TestClient(app) as client:
                    response = client.get(
                        "/api/plugins/waiting-studio/videos/waiting/source.mp4",
                        headers={"Range": "bytes=2-5"},
                    )
                    nested_response = client.get(
                        "/api/plugins/waiting-studio/videos/waiting/records%2Frecord.mp4",
                        headers={"Range": "bytes=3-6"},
                    )
                    finish_only = client.get("/api/plugins/waiting-studio/videos/waiting/finished.mp4")
                    traversal = client.get("/api/plugins/waiting-studio/videos/waiting/%2E%2E%2Foutside.mp4")

        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, b"2345")
        self.assertTrue(response.headers["content-type"].startswith("video/mp4"))
        self.assertEqual(response.headers["content-range"], "bytes 2-5/10")
        self.assertTrue(response.headers["content-disposition"].startswith("inline"))
        self.assertEqual(nested_response.status_code, 206)
        self.assertEqual(nested_response.content, b"defg")
        self.assertEqual(finish_only.status_code, 404)
        self.assertEqual(traversal.status_code, 404)

    def test_waiting_auto_codec_prefers_hvc1_copy_and_preserves_range_playback(self) -> None:
        if not HAS_FASTAPI:
            self.skipTest("FastAPI/Starlette is unavailable in this Python environment")
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory(dir="/opt/data/cache/scratch") as temp_dir:
            root = Path(temp_dir)
            home = root / "home"
            home.mkdir()
            media_root = root / "media"
            waiting = media_root / "Waiting" / "records"
            waiting.mkdir(parents=True)
            (media_root / "Finish").mkdir()
            (waiting / "source.mp4").write_bytes(b"original-video")
            compat = home / "state" / "waiting-studio" / "compat" / "records"
            compat.mkdir(parents=True)
            (compat / "source.mp4").write_bytes(b"hvc1-compatible-copy")

            app = FastAPI()
            app.include_router(plugin_api.router, prefix="/api/plugins/waiting-studio")
            with patch("plugin_api._media_root", return_value=media_root), patch(
                "plugin_api._references_home", return_value=home
            ):
                with TestClient(app) as client:
                    preferred = client.get(
                        "/api/plugins/waiting-studio/videos/waiting/records%2Fsource.mp4?codec=auto",
                        headers={"Range": "bytes=0-3"},
                    )
                    original = client.get(
                        "/api/plugins/waiting-studio/videos/waiting/records%2Fsource.mp4?codec=source",
                        headers={"Range": "bytes=0-2"},
                    )
                    invalid_codec = client.get(
                        "/api/plugins/waiting-studio/videos/waiting/records%2Fsource.mp4?codec=unknown"
                    )

        self.assertEqual(preferred.status_code, 206)
        self.assertEqual(preferred.content, b"hvc1")
        self.assertEqual(preferred.headers["content-range"], "bytes 0-3/20")
        self.assertEqual(original.status_code, 206)
        self.assertEqual(original.content, b"ori")
        self.assertEqual(original.headers["content-range"], "bytes 0-2/14")
        self.assertEqual(invalid_codec.status_code, 404)

    def test_content_workspace_creates_drafts_with_reference_context_snapshot(self) -> None:
        if not HAS_FASTAPI:
            self.skipTest("FastAPI/Starlette is unavailable in this Python environment")
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory(dir="/opt/data/cache/scratch") as temp_dir:
            base = Path(temp_dir)
            home = base / "home"
            home.mkdir()
            media_root = base / "media"
            (media_root / "Waiting").mkdir(parents=True)
            (media_root / "Finish").mkdir()
            source = media_root / "Waiting" / "source.mp4"
            source.write_bytes(b"original-source")
            (media_root / "Finish" / "finished.mp4").write_bytes(b"finish-source")
            saved_reference = plugin_api._add_reference(
                home,
                "https://www.youtube.com/shorts/abc123",
                "Observer le hook et le rythme.",
            )

            app = FastAPI()
            app.include_router(plugin_api.router, prefix="/api/plugins/waiting-studio")
            payload = {
                "format": "short",
                "title": "Premier essai",
                "concept": "Mettre en avant le moment fort du duel.",
                "hook": "Le retournement arrive tout de suite.",
                "script": "0–3 s : accroche; 3–12 s : action; 12–15 s : payoff.",
                "notes": "Ne pas couper le HUD.",
            }
            with patch("plugin_api._media_root", return_value=media_root), patch(
                "plugin_api._references_home", return_value=home
            ):
                with TestClient(app) as client:
                    initial = client.get("/api/plugins/waiting-studio/content/source.mp4")
                    created = client.post("/api/plugins/waiting-studio/content/source.mp4/items", json=payload)
                    created_moment = client.post(
                        "/api/plugins/waiting-studio/content/source.mp4/moments",
                        json={"timeSeconds": 123.456, "note": "Le retournement du duel.", "targetFormat": "short"},
                    )
                    invalid_moment = client.post(
                        "/api/plugins/waiting-studio/content/source.mp4/moments",
                        json={"timeSeconds": -1, "note": "Temps invalide", "targetFormat": "short"},
                    )
                    invalid_target = client.post(
                        "/api/plugins/waiting-studio/content/source.mp4/moments",
                        json={"timeSeconds": 12, "note": "Cible invalide", "targetFormat": []},
                    )
                    plugin_api._save_references(home, [{
                        **saved_reference,
                        "observationChecklist": [{"id": "hook", "label": "Consigne modifiée après création."}],
                    }])
                    listing = client.get("/api/plugins/waiting-studio/content/source.mp4")
                    invalid_format = client.post(
                        "/api/plugins/waiting-studio/content/source.mp4/items",
                        json={**payload, "format": "unknown"},
                    )
                    invalid_title = client.post(
                        "/api/plugins/waiting-studio/content/source.mp4/items",
                        json={**payload, "title": "  "},
                    )
                    missing_source = client.get("/api/plugins/waiting-studio/content/finished.mp4")
                    traversal = client.get("/api/plugins/waiting-studio/content/%2E%2E%2Foutside.mp4")
                    source_unchanged = source.read_bytes() == b"original-source"

        self.assertEqual(initial.status_code, 200)
        self.assertEqual(initial.json()["items"], [])
        self.assertEqual(initial.json()["moments"], [])
        self.assertEqual(initial.json()["references"][0]["id"], saved_reference["id"])
        self.assertEqual(created.status_code, 201)
        item = created.json()["item"]
        self.assertEqual(item["format"], "short")
        self.assertEqual(item["sourceFilename"], "source.mp4")
        self.assertEqual(item["creationContext"]["references"][0]["url"], saved_reference["url"])
        self.assertEqual(item["creationContext"]["references"][0]["note"], saved_reference["note"])
        self.assertEqual(item["creationContext"]["observationChecklist"], saved_reference["observationChecklist"])
        self.assertEqual(listing.json()["items"], [item])
        self.assertEqual(created_moment.status_code, 201)
        moment = created_moment.json()["moment"]
        self.assertEqual(moment["sourceFilename"], "source.mp4")
        self.assertEqual(moment["timeSeconds"], 123.456)
        self.assertEqual(moment["targetFormat"], "short")
        self.assertEqual(listing.json()["moments"], [moment])
        self.assertEqual(invalid_moment.status_code, 422)
        self.assertEqual(invalid_target.status_code, 422)
        self.assertEqual(invalid_format.status_code, 422)
        self.assertEqual(invalid_title.status_code, 422)
        self.assertEqual(missing_source.status_code, 404)
        self.assertEqual(traversal.status_code, 404)
        self.assertTrue(source_unchanged)

    def test_reference_api_adds_lists_and_removes_validated_links(self) -> None:
        if not HAS_FASTAPI:
            self.skipTest("FastAPI/Starlette is unavailable in this Python environment")
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory(dir="/opt/data/cache/scratch") as temp_dir:
            home = Path(temp_dir)
            app = FastAPI()
            app.include_router(plugin_api.router, prefix="/api/plugins/waiting-studio")
            payload = {
                "url": "https://www.youtube.com/shorts/abc123",
                "note": "Référence de rythme.",
            }
            with patch("plugin_api._references_home", return_value=home):
                with TestClient(app) as client:
                    created = client.post("/api/plugins/waiting-studio/references", json=payload)
                    listing = client.get("/api/plugins/waiting-studio/references")
                    reference_id = created.json()["reference"]["id"]
                    removed = client.delete(f"/api/plugins/waiting-studio/references/{reference_id}")
                    empty_listing = client.get("/api/plugins/waiting-studio/references")

        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["reference"]["platform"], "YouTube")
        self.assertEqual(listing.json()["references"][0]["note"], "Référence de rythme.")
        self.assertEqual(removed.status_code, 200)
        self.assertTrue(removed.json()["removed"])
        self.assertEqual(empty_listing.json()["references"], [])


if __name__ == "__main__":
    unittest.main()
