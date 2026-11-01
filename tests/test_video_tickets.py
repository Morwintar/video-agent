from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "dashboard"))
import plugin_api  # noqa: E402


class VideoTicketRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        tickets = getattr(plugin_api, "_STREAM_TICKETS", None)
        if tickets is not None:
            tickets.clear()

    def tearDown(self) -> None:
        tickets = getattr(plugin_api, "_STREAM_TICKETS", None)
        if tickets is not None:
            tickets.clear()

    def make_app(self) -> FastAPI:
        app = FastAPI()
        app.include_router(plugin_api.router, prefix="/api/plugins/video-agent")
        return app

    def test_ticket_streams_only_the_allowlisted_video_with_range_support(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            waiting = root / "Waiting" / "records"
            waiting.mkdir(parents=True)
            source = waiting / "vod.mp4"
            source.write_bytes(b"0123456789")
            (root / "Finish").mkdir()
            app = self.make_app()
            with patch("plugin_api._media_root", return_value=root):
                with TestClient(app) as client:
                    created = client.post("/api/plugins/video-agent/video-tickets", json={
                        "folderId": "waiting",
                        "filename": "records/vod.mp4",
                    })
                    self.assertEqual(created.status_code, 201)
                    payload = created.json()
                    stream_url = urlsplit(payload["streamUrl"])
                    renewed = client.post(
                        "/api/plugins/video-agent/video-tickets/renew",
                        json={"ticketId": payload["ticketId"]},
                    )
                    response = client.get(
                        f"{stream_url.path}?{stream_url.query}",
                        headers={"Range": "bytes=2-5"},
                    )
                    invalid_folder = client.post("/api/plugins/video-agent/video-tickets", json={
                        "folderId": "private",
                        "filename": "records/vod.mp4",
                    })
                    traversal = client.post("/api/plugins/video-agent/video-tickets", json={
                        "folderId": "waiting",
                        "filename": "../vod.mp4",
                    })

        self.assertEqual(created.status_code, 201)
        self.assertEqual(renewed.status_code, 200)
        self.assertEqual(renewed.json()["expiresInSeconds"], plugin_api._STREAM_TICKET_TTL_SECONDS)
        self.assertEqual(created.headers["cache-control"], "private, no-store")
        self.assertEqual(created.headers["referrer-policy"], "no-referrer")
        self.assertEqual(payload["expiresInSeconds"], plugin_api._STREAM_TICKET_TTL_SECONDS)
        self.assertNotIn(str(root), payload["streamUrl"])
        stream_path = urlsplit(payload["streamPath"])
        self.assertEqual(stream_path.path, "/videos/stream")
        self.assertEqual(stream_path.query, stream_url.query)
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, b"2345")
        self.assertTrue(response.headers["content-type"].startswith("video/mp4"))
        self.assertEqual(response.headers["content-range"], "bytes 2-5/10")
        self.assertTrue(response.headers["content-disposition"].startswith("inline"))
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        self.assertEqual(response.headers["referrer-policy"], "no-referrer")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(invalid_folder.status_code, 422)
        self.assertEqual(traversal.status_code, 404)

    def test_ticket_is_revoked_and_cannot_be_renewed_or_used_afterward(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            waiting = root / "Waiting"
            waiting.mkdir()
            (waiting / "vod.mp4").write_bytes(b"sample")
            app = self.make_app()
            with patch("plugin_api._media_root", return_value=root):
                with TestClient(app) as client:
                    created = client.post("/api/plugins/video-agent/video-tickets", json={
                        "folderId": "waiting",
                        "filename": "vod.mp4",
                    })
                    self.assertEqual(created.status_code, 201)
                    payload = created.json()
                    stream_url = urlsplit(payload["streamUrl"])
                    ticket_id = payload["ticketId"]
                    revoked = client.post(
                        "/api/plugins/video-agent/video-tickets/revoke",
                        json={"ticketId": ticket_id},
                    )
                    stream = client.get(f"{stream_url.path}?{stream_url.query}")
                    renewed = client.post(
                        "/api/plugins/video-agent/video-tickets/renew",
                        json={"ticketId": ticket_id},
                    )

        self.assertEqual(revoked.status_code, 200)
        self.assertTrue(revoked.json()["revoked"])
        self.assertEqual(stream.status_code, 404)
        self.assertEqual(renewed.status_code, 404)

    def test_waiting_ticket_streams_existing_hvc1_compat_remux(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            waiting = root / "Waiting"
            waiting.mkdir()
            (root / "Finish").mkdir()
            source = waiting / "vod.mp4"
            source.write_bytes(b"source-hev1-container")
            compat = root / "vod-hvc1.mp4"
            compat.write_bytes(b"compatible-hvc1-container")
            app = self.make_app()
            with patch("plugin_api._media_root", return_value=root), patch(
                "plugin_api._resolve_hvc1_compat_video", return_value=compat
            ):
                with TestClient(app) as client:
                    created = client.post("/api/plugins/video-agent/video-tickets", json={
                        "folderId": "waiting",
                        "filename": "vod.mp4",
                    })
                    self.assertEqual(created.status_code, 201)
                    stream_url = urlsplit(created.json()["streamUrl"])
                    response = client.get(
                        f"{stream_url.path}?{stream_url.query}",
                        headers={"Range": "bytes=0-9"},
                    )

        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, b"compatible-hvc1-container"[:10])
        self.assertEqual(
            response.headers["content-range"],
            f"bytes 0-9/{len(b'compatible-hvc1-container')}",
        )

    def test_replaced_hvc1_remux_is_not_used_by_an_existing_ticket(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            waiting = root / "Waiting"
            waiting.mkdir()
            (root / "Finish").mkdir()
            source = waiting / "vod.mp4"
            source.write_bytes(b"original-source")
            compat = root / "vod-hvc1.mp4"
            compat.write_bytes(b"original-compatible-remux")
            app = self.make_app()
            with patch("plugin_api._media_root", return_value=root), patch(
                "plugin_api._resolve_hvc1_compat_video", return_value=compat
            ):
                with TestClient(app) as client:
                    created = client.post("/api/plugins/video-agent/video-tickets", json={
                        "folderId": "waiting",
                        "filename": "vod.mp4",
                    })
                    self.assertEqual(created.status_code, 201)
                    stream_url = urlsplit(created.json()["streamUrl"])
                    compat.write_bytes(b"replacement-compatible-remux")
                    response = client.get(f"{stream_url.path}?{stream_url.query}")
                    source_bytes_after = source.read_bytes()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"original-source")
        self.assertEqual(source_bytes_after, b"original-source")

    def test_expired_ticket_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            waiting = root / "Waiting"
            waiting.mkdir()
            (waiting / "vod.mp4").write_bytes(b"sample")
            app = self.make_app()
            clock = [100.0]
            with patch("plugin_api._media_root", return_value=root), patch(
                "plugin_api.time.monotonic", side_effect=lambda: clock[0]
            ):
                with TestClient(app) as client:
                    created = client.post("/api/plugins/video-agent/video-tickets", json={
                        "folderId": "waiting",
                        "filename": "vod.mp4",
                    })
                    self.assertEqual(created.status_code, 201)
                    payload = created.json()
                    stream_url = urlsplit(payload["streamUrl"])
                    clock[0] += plugin_api._STREAM_TICKET_TTL_SECONDS + 1
                    response = client.get(f"{stream_url.path}?{stream_url.query}")

        self.assertEqual(response.status_code, 410)

    def test_ticket_stops_working_if_the_video_is_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            waiting = root / "Waiting"
            waiting.mkdir()
            source = waiting / "vod.mp4"
            source.write_bytes(b"before")
            app = self.make_app()
            with patch("plugin_api._media_root", return_value=root):
                with TestClient(app) as client:
                    created = client.post("/api/plugins/video-agent/video-tickets", json={
                        "folderId": "waiting",
                        "filename": "vod.mp4",
                    })
                    self.assertEqual(created.status_code, 201)
                    payload = created.json()
                    stream_url = urlsplit(payload["streamUrl"])
                    replacement = waiting / "replacement.mp4"
                    replacement.write_bytes(b"changed")
                    replacement.replace(source)
                    response = client.get(f"{stream_url.path}?{stream_url.query}")

        self.assertEqual(response.status_code, 404)

    def test_ticket_stream_rejects_symlinked_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            waiting = root / "Waiting"
            waiting.mkdir()
            outside = root / "outside.mp4"
            outside.write_bytes(b"outside")
            try:
                (waiting / "linked.mp4").symlink_to(outside)
            except OSError as exc:
                self.skipTest(f"Symlinks are unavailable: {exc}")
            app = self.make_app()
            with patch("plugin_api._media_root", return_value=root):
                with TestClient(app) as client:
                    response = client.post("/api/plugins/video-agent/video-tickets", json={
                        "folderId": "waiting",
                        "filename": "linked.mp4",
                    })

        self.assertEqual(response.status_code, 404)
        self.assertNotIn(str(root).encode(), response.content)


if __name__ == "__main__":
    unittest.main()
