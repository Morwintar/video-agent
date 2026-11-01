from __future__ import annotations

import json
import os
import shutil
import subprocess
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

    class _TestResponse:
        def __init__(self, content, *, media_type=None, headers=None, **_kwargs):
            self.body = content
            self.media_type = media_type
            self.headers = headers or {}

    fastapi_stub = types.ModuleType("fastapi")
    fastapi_stub.APIRouter = _TestRouter
    fastapi_stub.HTTPException = _TestHTTPException
    responses_stub = types.ModuleType("fastapi.responses")
    responses_stub.FileResponse = _TestFileResponse
    setattr(responses_stub, "Response", _TestResponse)
    fastapi_stub.responses = responses_stub
    sys.modules["fastapi"] = fastapi_stub
    sys.modules["fastapi.responses"] = responses_stub

import plugin_api  # noqa: E402
from plugin_api import _scan_folder, list_videos  # noqa: E402


class ScanFolderTests(unittest.TestCase):
    def test_lists_sorted_regular_mp4_files_and_skips_other_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
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
        with tempfile.TemporaryDirectory() as temp_dir:
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
        with tempfile.TemporaryDirectory() as temp_dir:
            result = _scan_folder(Path(temp_dir), "finish", "Finish")

        self.assertFalse(result["available"])
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["videos"], [])
        self.assertNotIn("path", result)

    def test_relative_workspace_override_is_rejected(self) -> None:
        with patch.dict(os.environ, {"VIDEO_AGENT_WORKSPACE": "relative/workspace"}):
            self.assertIsNone(plugin_api._workspace_root())

    def test_mounted_workspace_lists_waiting_references_and_finish_instead_of_legacy_media(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plugin_root = root / "plugin"
            workspace = plugin_root / "workspace"
            for label in ("Waiting", "References", "Finish"):
                (workspace / label).mkdir(parents=True, exist_ok=True)
            (workspace / "Waiting" / "incoming.mp4").write_bytes(b"waiting")
            (workspace / "References" / "style.mp4").write_bytes(b"reference")
            (workspace / "Finish" / "final.mp4").write_bytes(b"finished")
            home = root / "hermes"
            legacy_waiting = home / "inbox" / "stream-test" / "Waiting"
            legacy_waiting.mkdir(parents=True)
            (legacy_waiting / "legacy.mp4").write_bytes(b"legacy")

            with patch.dict(os.environ, {"HERMES_HOME": str(home), "VIDEO_AGENT_WORKSPACE": str(workspace)}), patch(
                "plugin_api._plugin_root", return_value=plugin_root
            ):
                payload = list_videos()

        self.assertEqual(
            [folder["label"] for folder in payload["folders"]],
            ["Waiting", "References", "Finish"],
        )
        self.assertEqual([folder["count"] for folder in payload["folders"]], [1, 1, 1])
        self.assertEqual(
            [video["name"] for folder in payload["folders"] for video in folder["videos"]],
            ["incoming.mp4", "style.mp4", "final.mp4"],
        )

    def test_route_lists_waiting_references_and_finish_from_active_hermes_home(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            media_root = home / "inbox" / "stream-test"
            waiting = media_root / "Waiting"
            references = media_root / "References"
            finish = media_root / "Finish"
            extra = media_root / "Unrelated"
            for directory in (waiting, references, finish, extra):
                directory.mkdir(parents=True)
            (waiting / "incoming.mp4").write_bytes(b"w")
            (waiting / "records").mkdir()
            (waiting / "records" / "local-recording.mp4").write_bytes(b"wn")
            (references / "reference.mp4").write_bytes(b"r")
            (finish / "render.mp4").write_bytes(b"f")
            (finish / "subfolder").mkdir()
            (finish / "subfolder" / "ignored.mp4").write_bytes(b"f2")
            (extra / "not-listed.mp4").write_bytes(b"x")

            with patch.dict(os.environ, {"HERMES_HOME": str(home)}), patch(
                "plugin_api._plugin_root", return_value=home / "plugins" / "video-agent"
            ):
                payload = list_videos()

        self.assertTrue(payload["readOnly"])
        self.assertEqual(
            [folder["label"] for folder in payload["folders"]],
            ["Waiting", "References", "Finish"],
        )
        self.assertEqual([folder["count"] for folder in payload["folders"]], [2, 1, 2])
        self.assertEqual(
            [video["name"] for folder in payload["folders"] for video in folder["videos"]],
            ["incoming.mp4", "records/local-recording.mp4", "reference.mp4", "render.mp4", "subfolder/ignored.mp4"],
        )
        self.assertTrue(
            all("path" not in video for folder in payload["folders"] for video in folder["videos"])
        )


class NewFeatureTests(unittest.TestCase):
    def test_finish_video_resolver_is_confined_to_regular_finish_mp4s(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            finish = root / "Finish"
            waiting = root / "Waiting"
            finish.mkdir()
            waiting.mkdir()
            (finish / "final.mp4").write_bytes(b"video")
            nested_finish = finish / "events"
            nested_finish.mkdir()
            (nested_finish / "nested-final.mp4").write_bytes(b"nested")
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
            self.assertEqual(resolver(root, "events/nested-final.mp4"), nested_finish / "nested-final.mp4")
            with patch("plugin_api._media_root", return_value=root):
                response = plugin_api.get_finished_video("events/nested-final.mp4")
            self.assertEqual(Path(response.path), nested_finish / "nested-final.mp4")
            self.assertIsNone(resolver(root, "../outside.mp4"))
            self.assertIsNone(resolver(root, "incoming.mp4"))
            self.assertIsNone(resolver(root, "linked.mp4"))
            self.assertIsNone(resolver(root, "notes.txt"))

    def test_finish_video_route_supports_range_playback(self) -> None:
        if not HAS_FASTAPI:
            self.skipTest("FastAPI/Starlette is unavailable in this Python environment")
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            finish = root / "Finish"
            finish.mkdir()
            events = finish / "events"
            events.mkdir()
            (events / "final.mp4").write_bytes(b"0123456789")
            self.assertTrue(hasattr(plugin_api, "get_finished_video"), "finish video endpoint is missing")
            app = FastAPI()
            app.include_router(plugin_api.router, prefix="/api/plugins/video-agent")
            with patch("plugin_api._media_root", return_value=root):
                with TestClient(app) as client:
                    response = client.get(
                        "/api/plugins/video-agent/videos/finish/events/final.mp4",
                        headers={"Range": "bytes=2-5"},
                    )

        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, b"2345")
        self.assertTrue(response.headers["content-type"].startswith("video/mp4"))
        self.assertEqual(response.headers["content-range"], "bytes 2-5/10")
        self.assertTrue(response.headers["content-disposition"].startswith("inline"))

    def test_reference_video_resolver_is_confined_to_regular_reference_mp4s(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            references = root / "References"
            references.mkdir()
            nested = references / "vertical"
            nested.mkdir()
            (nested / "style.mp4").write_bytes(b"reference")
            (root / "outside.mp4").write_bytes(b"outside")
            self.assertTrue(
                hasattr(plugin_api, "_resolve_reference_video"),
                "reference video resolver is missing",
            )
            self.assertTrue(
                hasattr(plugin_api, "get_reference_video"),
                "reference video endpoint is missing",
            )
            resolver = plugin_api._resolve_reference_video
            self.assertEqual(resolver(root, "vertical/style.mp4"), nested / "style.mp4")
            self.assertIsNone(resolver(root, "../outside.mp4"))
            self.assertIsNone(resolver(root, "outside.mp4"))
            self.assertIsNone(resolver(root, "notes.txt"))
            with patch("plugin_api._media_root", return_value=root):
                response = plugin_api.get_reference_video("vertical/style.mp4")
            self.assertEqual(Path(response.path), nested / "style.mp4")
            self.assertEqual(response.media_type, "video/mp4")

    def test_reference_video_route_supports_range_playback(self) -> None:
        if not HAS_FASTAPI:
            self.skipTest("FastAPI/Starlette is unavailable in this Python environment")
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            references = root / "References"
            references.mkdir()
            nested = references / "vertical"
            nested.mkdir()
            (nested / "style.mp4").write_bytes(b"0123456789")
            app = FastAPI()
            app.include_router(plugin_api.router, prefix="/api/plugins/video-agent")
            with patch("plugin_api._media_root", return_value=root):
                with TestClient(app) as client:
                    response = client.get(
                        "/api/plugins/video-agent/videos/references/vertical%2Fstyle.mp4",
                        headers={"Range": "bytes=2-5"},
                    )

        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, b"2345")
        self.assertTrue(response.headers["content-type"].startswith("video/mp4"))
        self.assertEqual(response.headers["content-range"], "bytes 2-5/10")
        self.assertTrue(response.headers["content-disposition"].startswith("inline"))

    def test_workspace_uses_legacy_links_until_the_first_workspace_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plugin_root = root / "plugin"
            workspace = plugin_root / "workspace"
            home = root / "hermes"
            home.mkdir()
            with patch.dict(os.environ, {"HERMES_HOME": str(home), "VIDEO_AGENT_WORKSPACE": str(workspace)}), patch(
                "plugin_api._plugin_root", return_value=plugin_root
            ):
                legacy_reference = plugin_api._add_reference(
                    plugin_api._references_home(),
                    "https://youtu.be/legacy123",
                    "Ancienne note.",
                )
                legacy_store = home / "state" / "waiting-studio" / "references.json"
                self.assertTrue(legacy_store.is_file(), "legacy store should exist before the mount is activated")
                for label in ("Waiting", "References", "Finish"):
                    (workspace / label).mkdir(parents=True, exist_ok=True)
                visible = plugin_api._list_references(plugin_api._references_home())
                new_reference = plugin_api._add_reference(
                    plugin_api._references_home(),
                    "https://youtu.be/newref123",
                    "Nouvelle note.",
                )
                migrated = plugin_api._list_references(plugin_api._references_home())
                old_store_after = legacy_store.is_file()

            workspace_store = workspace / "References" / "links.json"
            mode = workspace_store.stat().st_mode & 0o777

        self.assertEqual([item["id"] for item in visible], [legacy_reference["id"]])
        self.assertEqual({item["id"] for item in migrated}, {legacy_reference["id"], new_reference["id"]})
        self.assertTrue(old_store_after)
        self.assertEqual(mode, 0o600)

    def test_workspace_persists_reference_links_beside_reference_media(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plugin_root = root / "plugin"
            workspace = plugin_root / "workspace"
            for label in ("Waiting", "References", "Finish"):
                (workspace / label).mkdir(parents=True, exist_ok=True)
            home = root / "hermes"
            home.mkdir()
            with patch.dict(os.environ, {"HERMES_HOME": str(home), "VIDEO_AGENT_WORKSPACE": str(workspace)}), patch(
                "plugin_api._plugin_root", return_value=plugin_root
            ):
                reference = plugin_api._add_reference(
                    plugin_api._references_home(),
                    "https://youtu.be/example123",
                    "Observer le rythme.",
                )

            workspace_store = workspace / "References" / "links.json"
            legacy_store = home / "state" / "waiting-studio" / "references.json"
            payload = json.loads(workspace_store.read_text(encoding="utf-8"))

        self.assertEqual(payload["references"][0]["id"], reference["id"])
        self.assertFalse(legacy_store.exists())

    def test_custom_skill_creation_is_plugin_local_and_listed_without_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
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

        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_root = Path(temp_dir)
            app = FastAPI()
            app.include_router(plugin_api.router, prefix="/api/plugins/video-agent")
            payload = {
                "name": "clip-review",
                "description": "Review clips before export.",
                "instructions": "## Checklist\n- Preserve the source.",
            }
            with patch("plugin_api._plugin_root", return_value=plugin_root):
                with TestClient(app) as client:
                    created = client.post("/api/plugins/video-agent/skills", json=payload)
                    listing = client.get("/api/plugins/video-agent/skills")
                    duplicate = client.post("/api/plugins/video-agent/skills", json=payload)
                    invalid = client.post(
                        "/api/plugins/video-agent/skills",
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

        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_root = Path(temp_dir)
            shutil.copyfile(PLUGIN_ROOT / "plugin.json", plugin_root / "plugin.json")
            plugin_api._create_custom_skill(
                plugin_root,
                "clip-review",
                "Review finished clips before export.",
                "## Checklist\n- Preserve the source video.",
            )
            package = load_agent_plugin(plugin_root, plugin_root / "data")

        self.assertEqual(package.name, "video-agent")
        self.assertEqual([skill.name for skill in package.skills], ["clip-review"])
        self.assertEqual(package.diagnostics, ())

    def test_custom_skill_creation_rejects_traversal_and_existing_names(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
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
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            with patch.dict(os.environ, {"VIDEO_AGENT_WORKSPACE": str(home / "missing-workspace")}):
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

    def test_new_reference_is_queued_for_automatic_download(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            with patch.dict(os.environ, {"VIDEO_AGENT_WORKSPACE": str(home / "missing-workspace")}):
                reference = plugin_api._add_reference(
                    home,
                    "https://youtu.be/abc123",
                    "Observer le style.",
                )
                persisted = plugin_api._list_references(home)[0]

        self.assertEqual(reference["downloadStatus"], "queued")
        self.assertEqual(persisted["downloadStatus"], "queued")

    def test_deleting_reference_removes_only_its_managed_video_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "workspace"
            references_dir = workspace / "References"
            downloads = references_dir / "downloads"
            for name in ("Waiting", "Finish"):
                (workspace / name).mkdir(parents=True, exist_ok=True)
            downloads.mkdir(parents=True)
            unrelated = downloads / "unrelated.mp4"
            unrelated.write_bytes(b"keep")
            home = root / "home"
            home.mkdir()
            with patch.dict(os.environ, {"VIDEO_AGENT_WORKSPACE": str(workspace)}):
                reference = plugin_api._add_reference(home, "https://youtu.be/abc123", "")
                local_copy = references_dir / f"{reference['id']}.mp4"
                local_copy.write_bytes(b"downloaded")
                reference["videoFile"] = f"{reference['id']}.mp4"
                reference["downloadStatus"] = "completed"
                plugin_api._save_references(home, [reference])

                self.assertTrue(plugin_api._delete_reference(home, reference["id"]))

                self.assertFalse(local_copy.exists())
                self.assertEqual(unrelated.read_bytes(), b"keep")

    def test_legacy_downloads_subfolder_copy_remains_available_and_deletable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "workspace"
            references_dir = workspace / "References"
            legacy_dir = references_dir / "downloads"
            for name in ("Waiting", "Finish"):
                (workspace / name).mkdir(parents=True, exist_ok=True)
            legacy_dir.mkdir(parents=True)
            home = root / "home"
            home.mkdir()
            with patch.dict(os.environ, {"VIDEO_AGENT_WORKSPACE": str(workspace)}):
                reference = plugin_api._add_reference(home, "https://youtu.be/abc123", "")
                local_copy = legacy_dir / f"{reference['id']}.mp4"
                local_copy.write_bytes(b"legacy download")
                reference["videoFile"] = f"downloads/{reference['id']}.mp4"
                reference["downloadStatus"] = "completed"
                plugin_api._save_references(home, [reference])

                self.assertTrue(plugin_api._reference_video_is_available(reference))
                self.assertTrue(plugin_api._delete_reference(home, reference["id"]))

            self.assertFalse(local_copy.exists())

    def test_list_references_migrates_legacy_managed_copy_into_references_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "workspace"
            references_dir = workspace / "References"
            legacy_dir = references_dir / "downloads"
            for name in ("Waiting", "Finish"):
                (workspace / name).mkdir(parents=True, exist_ok=True)
            legacy_dir.mkdir(parents=True)
            home = root / "home"
            home.mkdir()
            with patch.dict(os.environ, {"VIDEO_AGENT_WORKSPACE": str(workspace), "HERMES_HOME": str(home)}):
                reference = plugin_api._add_reference(home, "https://youtu.be/abc123", "")
                legacy_copy = legacy_dir / f"{reference['id']}.mp4"
                direct_copy = references_dir / f"{reference['id']}.mp4"
                legacy_copy.write_bytes(b"legacy managed video")
                reference["videoFile"] = f"downloads/{reference['id']}.mp4"
                reference["downloadStatus"] = "completed"
                plugin_api._save_references(home, [reference])

                response = plugin_api.list_references()

            self.assertEqual(response["references"][0]["videoFile"], f"{reference['id']}.mp4")
            self.assertEqual(response["references"][0]["downloadStatus"], "completed")
            self.assertFalse(legacy_copy.exists())
            self.assertEqual(direct_copy.read_bytes(), b"legacy managed video")
            self.assertEqual(
                plugin_api._scan_folder(workspace, "references", "References")["videos"],
                [{"name": f"{reference['id']}.mp4", "sizeBytes": len(b"legacy managed video")}],
            )

    def test_reference_downloader_saves_one_managed_mp4_and_updates_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "workspace"
            for label in ("Waiting", "References", "Finish"):
                (workspace / label).mkdir(parents=True, exist_ok=True)
            home = root / "home"
            home.mkdir()
            url = "https://youtu.be/abc123"
            with patch.dict(os.environ, {"VIDEO_AGENT_WORKSPACE": str(workspace)}):
                reference = plugin_api._add_reference(home, url, "Style")

                class FakeYoutubeDL:
                    def __init__(self, options):
                        self.options = options

                    def __enter__(self):
                        return self

                    def __exit__(self, *_args):
                        return None

                    def download(self, urls):
                        self.assertions = (urls, self.options)
                        output = Path(self.options["outtmpl"].replace("%(ext)s", "mp4"))
                        output.write_bytes(b"test MP4")
                        return 0

                downloader_module = types.SimpleNamespace(YoutubeDL=FakeYoutubeDL)
                with patch.dict(sys.modules, {"yt_dlp": downloader_module}):
                    self.assertTrue(hasattr(plugin_api, "_download_reference_video"))
                    plugin_api._download_reference_video(home, reference["id"])

                persisted = plugin_api._list_references(home)[0]
                output = workspace / "References" / f"{reference['id']}.mp4"
                inventory = plugin_api._scan_folder(workspace, "references", "References")

            self.assertEqual(persisted["downloadStatus"], "completed")
            self.assertEqual(persisted["videoFile"], f"{reference['id']}.mp4")
            self.assertEqual(output.read_bytes(), b"test MP4")
            self.assertEqual(inventory["videos"], [{"name": f"{reference['id']}.mp4", "sizeBytes": len(b"test MP4")}])
            self.assertFalse((workspace / "References" / "downloads").exists())
            self.assertFalse((workspace / ".video-agent-downloads" / reference["id"]).exists())

    def test_new_references_capture_the_default_observation_checklist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("plugin_api._active_workspace_root", return_value=None):
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

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            home = root / "home"
            home.mkdir()
            media_root = root / "media"
            waiting = media_root / "Waiting" / "records"
            waiting.mkdir(parents=True)
            (media_root / "Finish").mkdir()
            (waiting / "nested-recording.mp4").write_bytes(b"video")
            app = FastAPI()
            app.include_router(plugin_api.router, prefix="/api/plugins/video-agent")
            with patch("plugin_api._media_root", return_value=media_root), patch(
                "plugin_api._references_home", return_value=home
            ), patch("plugin_api._active_workspace_root", return_value=None):
                with TestClient(app) as client:
                    workspace = client.get("/api/plugins/video-agent/content/records%2Fnested-recording.mp4")
                    moment = client.post(
                        "/api/plugins/video-agent/content/records%2Fnested-recording.mp4/moments",
                        json={"timeSeconds": 12.5, "note": "Moment fort", "targetFormat": "short"},
                    )
                    draft = client.post(
                        "/api/plugins/video-agent/content/records%2Fnested-recording.mp4/items",
                        json={"format": "short", "title": "Le duel", "concept": "Retour spectaculaire"},
                    )

        self.assertEqual(workspace.status_code, 200)
        self.assertEqual(workspace.json()["sourceFilename"], "records/nested-recording.mp4")
        self.assertEqual(moment.status_code, 201)
        self.assertEqual(moment.json()["moment"]["sourceFilename"], "records/nested-recording.mp4")
        self.assertEqual(draft.status_code, 201)
        self.assertEqual(draft.json()["item"]["sourceFilename"], "records/nested-recording.mp4")

    def test_content_workspace_lists_only_finish_clips_for_selected_waiting_video(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            home = root / "home"
            home.mkdir()
            waiting = root / "Waiting" / "records"
            waiting.mkdir(parents=True)
            finish = root / "Finish"
            finish.mkdir()
            (waiting / "vod.mp4").write_bytes(b"source")
            (finish / "vod_highlight_15s.mp4").write_bytes(b"clip")
            (finish / "vod.mp4").write_bytes(b"not a clip")
            (finish / "vodder_highlight.mp4").write_bytes(b"different source")
            (finish / "other_vod_highlight.mp4").write_bytes(b"different source")
            nested_finish = finish / "nested"
            nested_finish.mkdir()
            (nested_finish / "vod_nested_clip.mp4").write_bytes(b"not direct")
            outside = root / "outside.mp4"
            outside.write_bytes(b"linked clip")
            try:
                (finish / "vod_linked_clip.mp4").symlink_to(outside)
            except OSError as exc:
                self.skipTest(f"Symlinks are unavailable: {exc}")

            workspace = plugin_api._get_content_workspace(root, home, "records/vod.mp4")

        self.assertEqual(workspace["sourceFilename"], "records/vod.mp4")
        self.assertEqual(workspace["clips"], [{"name": "vod_highlight_15s.mp4", "sizeBytes": 4}])
        self.assertNotIn("path", workspace["clips"][0])

    def test_waiting_video_resolver_accepts_nested_regular_mp4s_but_rejects_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
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

    def test_waiting_thumbnail_route_returns_jpeg_and_rejects_unsafe_paths(self) -> None:
        if not HAS_FASTAPI:
            self.skipTest("FastAPI/Starlette is unavailable in this Python environment")
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            self.skipTest("FFmpeg is unavailable in this Python environment")
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            waiting = root / "Waiting" / "records"
            waiting.mkdir(parents=True)
            source = waiting / "sample.mp4"
            subprocess.run(
                [
                    ffmpeg,
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=red:s=64x36:r=1:d=1",
                    "-an",
                    "-c:v",
                    "mpeg4",
                    "-q:v",
                    "5",
                    "-y",
                    str(source),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=20,
            )
            outside = root / "outside.mp4"
            outside.write_bytes(source.read_bytes())
            broken = waiting / "broken.mp4"
            broken.write_bytes(b"not a video")
            try:
                (waiting / "linked.mp4").symlink_to(outside)
            except OSError as exc:
                self.skipTest(f"Symlinks are unavailable: {exc}")

            app = FastAPI()
            app.include_router(plugin_api.router, prefix="/api/plugins/video-agent")
            with patch("plugin_api._media_root", return_value=root):
                with TestClient(app) as client:
                    response = client.get(
                        "/api/plugins/video-agent/videos/waiting-thumbnails/records%2Fsample.mp4"
                    )
                    traversal = client.get(
                        "/api/plugins/video-agent/videos/waiting-thumbnails/%2E%2E%2Foutside.mp4"
                    )
                    linked = client.get(
                        "/api/plugins/video-agent/videos/waiting-thumbnails/linked.mp4"
                    )
                    invalid = client.get(
                        "/api/plugins/video-agent/videos/waiting-thumbnails/records%2Fbroken.mp4"
                    )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("image/jpeg"))
        self.assertTrue(response.content.startswith(b"\xff\xd8"))
        self.assertTrue(response.content.endswith(b"\xff\xd9"))
        self.assertEqual(response.headers["cache-control"], "private, max-age=3600")
        self.assertEqual(traversal.status_code, 404)
        self.assertEqual(linked.status_code, 404)
        self.assertEqual(invalid.status_code, 503)
        self.assertNotIn(b"Invalid data", invalid.content)

    def test_waiting_video_route_supports_range_playback(self) -> None:
        if not HAS_FASTAPI:
            self.skipTest("FastAPI/Starlette is unavailable in this Python environment")
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory() as temp_dir:
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
            app.include_router(plugin_api.router, prefix="/api/plugins/video-agent")
            with patch("plugin_api._media_root", return_value=root):
                with TestClient(app) as client:
                    response = client.get(
                        "/api/plugins/video-agent/videos/waiting/source.mp4",
                        headers={"Range": "bytes=2-5"},
                    )
                    nested_response = client.get(
                        "/api/plugins/video-agent/videos/waiting/records%2Frecord.mp4",
                        headers={"Range": "bytes=3-6"},
                    )
                    finish_only = client.get("/api/plugins/video-agent/videos/waiting/finished.mp4")
                    traversal = client.get("/api/plugins/video-agent/videos/waiting/%2E%2E%2Foutside.mp4")

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

        with tempfile.TemporaryDirectory() as temp_dir:
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
            app.include_router(plugin_api.router, prefix="/api/plugins/video-agent")
            with patch("plugin_api._media_root", return_value=media_root), patch(
                "plugin_api._references_home", return_value=home
            ), patch("plugin_api._active_workspace_root", return_value=None):
                with TestClient(app) as client:
                    preferred = client.get(
                        "/api/plugins/video-agent/videos/waiting/records%2Fsource.mp4?codec=auto",
                        headers={"Range": "bytes=0-3"},
                    )
                    original = client.get(
                        "/api/plugins/video-agent/videos/waiting/records%2Fsource.mp4?codec=source",
                        headers={"Range": "bytes=0-2"},
                    )
                    invalid_codec = client.get(
                        "/api/plugins/video-agent/videos/waiting/records%2Fsource.mp4?codec=unknown"
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

        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            home = base / "home"
            home.mkdir()
            media_root = base / "media"
            (media_root / "Waiting").mkdir(parents=True)
            (media_root / "Finish").mkdir()
            source = media_root / "Waiting" / "source.mp4"
            source.write_bytes(b"original-source")
            (media_root / "Finish" / "finished.mp4").write_bytes(b"finish-source")
            with patch("plugin_api._active_workspace_root", return_value=None):
                saved_reference = plugin_api._add_reference(
                    home,
                    "https://www.youtube.com/shorts/abc123",
                    "Observer le hook et le rythme.",
                )

            app = FastAPI()
            app.include_router(plugin_api.router, prefix="/api/plugins/video-agent")
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
            ), patch("plugin_api._active_workspace_root", return_value=None):
                with TestClient(app) as client:
                    initial = client.get("/api/plugins/video-agent/content/source.mp4")
                    created = client.post("/api/plugins/video-agent/content/source.mp4/items", json=payload)
                    created_moment = client.post(
                        "/api/plugins/video-agent/content/source.mp4/moments",
                        json={"timeSeconds": 123.456, "note": "Le retournement du duel.", "targetFormat": "short"},
                    )
                    invalid_moment = client.post(
                        "/api/plugins/video-agent/content/source.mp4/moments",
                        json={"timeSeconds": -1, "note": "Temps invalide", "targetFormat": "short"},
                    )
                    invalid_target = client.post(
                        "/api/plugins/video-agent/content/source.mp4/moments",
                        json={"timeSeconds": 12, "note": "Cible invalide", "targetFormat": []},
                    )
                    plugin_api._save_references(home, [{
                        **saved_reference,
                        "observationChecklist": [{"id": "hook", "label": "Consigne modifiée après création."}],
                    }])
                    listing = client.get("/api/plugins/video-agent/content/source.mp4")
                    invalid_format = client.post(
                        "/api/plugins/video-agent/content/source.mp4/items",
                        json={**payload, "format": "unknown"},
                    )
                    invalid_title = client.post(
                        "/api/plugins/video-agent/content/source.mp4/items",
                        json={**payload, "title": "  "},
                    )
                    missing_source = client.get("/api/plugins/video-agent/content/finished.mp4")
                    traversal = client.get("/api/plugins/video-agent/content/%2E%2E%2Foutside.mp4")
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

    def test_reference_api_add_queues_a_video_download(self) -> None:
        if not HAS_FASTAPI:
            self.skipTest("FastAPI/Starlette is unavailable in this Python environment")
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            home = root / "home"
            home.mkdir()
            workspace = root / "workspace"
            for label in ("Waiting", "References", "Finish"):
                (workspace / label).mkdir(parents=True, exist_ok=True)
            app = FastAPI()
            app.include_router(plugin_api.router, prefix="/api/plugins/video-agent")
            with patch.dict(os.environ, {"HERMES_HOME": str(home), "VIDEO_AGENT_WORKSPACE": str(workspace)}), patch(
                "plugin_api._schedule_reference_download"
            ) as schedule_download:
                with TestClient(app) as client:
                    created = client.post(
                        "/api/plugins/video-agent/references",
                        json={"url": "https://youtu.be/abc123", "note": "Rythme"},
                    )

        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["reference"]["downloadStatus"], "queued")
        schedule_download.assert_called_once_with(home, created.json()["reference"]["id"])

    def test_existing_reference_without_local_copy_can_be_queued_from_api(self) -> None:
        if not HAS_FASTAPI:
            self.skipTest("FastAPI/Starlette is unavailable in this Python environment")
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            home = root / "home"
            home.mkdir()
            workspace = root / "workspace"
            for label in ("Waiting", "References", "Finish"):
                (workspace / label).mkdir(parents=True, exist_ok=True)
            app = FastAPI()
            app.include_router(plugin_api.router, prefix="/api/plugins/video-agent")
            with patch.dict(os.environ, {"HERMES_HOME": str(home), "VIDEO_AGENT_WORKSPACE": str(workspace)}):
                reference = plugin_api._add_reference(home, "https://youtu.be/abc123", "Older reference")
                reference.pop("downloadStatus")
                plugin_api._save_references(home, [reference])
                with patch("plugin_api._schedule_reference_download") as schedule_download:
                    with TestClient(app) as client:
                        queued = client.post(
                            f"/api/plugins/video-agent/references/{reference['id']}/download"
                        )

                self.assertEqual(queued.status_code, 202)
                self.assertEqual(queued.json()["reference"]["downloadStatus"], "queued")
                schedule_download.assert_called_once_with(home, reference["id"])

    def test_reference_api_marks_missing_completed_copy_as_downloadable(self) -> None:
        if not HAS_FASTAPI:
            self.skipTest("FastAPI/Starlette is unavailable in this Python environment")
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            home = root / "home"
            home.mkdir()
            workspace = root / "workspace"
            for label in ("Waiting", "References", "Finish"):
                (workspace / label).mkdir(parents=True, exist_ok=True)
            app = FastAPI()
            app.include_router(plugin_api.router, prefix="/api/plugins/video-agent")
            with patch.dict(os.environ, {"HERMES_HOME": str(home), "VIDEO_AGENT_WORKSPACE": str(workspace)}):
                reference = plugin_api._add_reference(home, "https://youtu.be/abc123", "Previously downloaded")
                reference["downloadStatus"] = "completed"
                reference["videoFile"] = f"downloads/{reference['id']}.mp4"
                plugin_api._save_references(home, [reference])
                with patch("plugin_api._schedule_reference_download") as schedule_download:
                    with TestClient(app) as client:
                        listed = client.get("/api/plugins/video-agent/references")
                        queued = client.post(f"/api/plugins/video-agent/references/{reference['id']}/download")

                self.assertEqual(listed.status_code, 200)
                self.assertEqual(listed.json()["references"][0]["downloadStatus"], "not_requested")
                self.assertEqual(queued.status_code, 202)
                schedule_download.assert_called_once_with(home, reference["id"])

    def test_reference_api_adds_lists_and_removes_validated_links(self) -> None:
        if not HAS_FASTAPI:
            self.skipTest("FastAPI/Starlette is unavailable in this Python environment")
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            app = FastAPI()
            app.include_router(plugin_api.router, prefix="/api/plugins/video-agent")
            payload = {
                "url": "https://www.youtube.com/shorts/abc123",
                "note": "Référence de rythme.",
            }
            with patch("plugin_api._references_home", return_value=home), patch(
                "plugin_api._active_workspace_root", return_value=None
            ):
                with TestClient(app) as client:
                    created = client.post("/api/plugins/video-agent/references", json=payload)
                    listing = client.get("/api/plugins/video-agent/references")
                    reference_id = created.json()["reference"]["id"]
                    removed = client.delete(f"/api/plugins/video-agent/references/{reference_id}")
                    empty_listing = client.get("/api/plugins/video-agent/references")

        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["reference"]["platform"], "YouTube")
        self.assertEqual(listing.json()["references"][0]["note"], "Référence de rythme.")
        self.assertEqual(removed.status_code, 200)
        self.assertTrue(removed.json()["removed"])
        self.assertEqual(empty_listing.json()["references"], [])


if __name__ == "__main__":
    unittest.main()
