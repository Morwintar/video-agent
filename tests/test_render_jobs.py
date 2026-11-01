from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "dashboard"))

import plugin_api  # noqa: E402


class RenderJobApiTests(unittest.TestCase):
    def test_selected_moments_can_be_queued_as_vertical_clips(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            base = Path(temporary_directory)
            home = base / "home"
            home.mkdir()
            media_root = base / "workspace"
            source = media_root / "Waiting" / "records" / "source.mp4"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"fixture-video")
            (media_root / "References").mkdir()
            (media_root / "Finish").mkdir()
            moment = plugin_api._create_video_moment(
                media_root,
                home,
                "records/source.mp4",
                {"timeSeconds": 42.0, "note": "Réaction", "targetFormat": "short"},
            )
            app = FastAPI()
            app.include_router(plugin_api.router, prefix="/api/plugins/video-agent")

            with patch("plugin_api._media_root", return_value=media_root), patch(
                "plugin_api._references_home", return_value=home
            ), patch("plugin_api._schedule_render_job", create=True) as schedule_render_job:
                with TestClient(app) as client:
                    response = client.post(
                        "/api/plugins/video-agent/content/records%2Fsource.mp4/renders",
                        json={
                            "kind": "clips",
                            "momentIds": [moment["id"]],
                            "clipDurationSeconds": 18,
                        },
                    )
                    invalid_response = client.post(
                        "/api/plugins/video-agent/content/records%2Fsource.mp4/renders",
                        json={
                            "kind": [],
                            "momentIds": [moment["id"]],
                            "clipDurationSeconds": 18,
                        },
                    )
                    listing = client.get("/api/plugins/video-agent/content/records%2Fsource.mp4")

        self.assertEqual(response.status_code, 202)
        job = response.json()["job"]
        self.assertEqual(job["kind"], "clips")
        self.assertEqual(job["sourceFilename"], "records/source.mp4")
        self.assertEqual(job["momentIds"], [moment["id"]])
        self.assertEqual(job["clipDurationSeconds"], 18)
        self.assertEqual(job["status"], "queued")
        schedule_render_job.assert_called_once_with(home, job["id"])
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(invalid_response.status_code, 422)
        self.assertEqual(listing.json()["renderJobs"], [job])

    def test_best_of_rejects_duration_over_thirty_minutes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            base = Path(temporary_directory)
            home = base / "home"
            home.mkdir()
            media_root = base / "workspace"
            source = media_root / "Waiting" / "source.mp4"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"fixture-video")
            (media_root / "References").mkdir()
            (media_root / "Finish").mkdir()
            moments = [
                plugin_api._create_video_moment(
                    media_root,
                    home,
                    "source.mp4",
                    {"timeSeconds": float(index * 20), "note": f"Moment {index}", "targetFormat": "short"},
                )
                for index in range(91)
            ]
            app = FastAPI()
            app.include_router(plugin_api.router, prefix="/api/plugins/video-agent")
            with patch("plugin_api._media_root", return_value=media_root), patch(
                "plugin_api._references_home", return_value=home
            ), patch("plugin_api._schedule_render_job", create=True) as schedule_render_job:
                with TestClient(app) as client:
                    response = client.post(
                        "/api/plugins/video-agent/content/source.mp4/renders",
                        json={
                            "kind": "best_of",
                            "momentIds": [moment["id"] for moment in moments],
                            "clipDurationSeconds": 20,
                        },
                    )

        self.assertEqual(response.status_code, 422)
        schedule_render_job.assert_not_called()

    def test_best_of_worker_assembles_selected_copies_and_keeps_source_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            base = Path(temporary_directory)
            home = base / "home"
            home.mkdir()
            media_root = base / "workspace"
            source = media_root / "Waiting" / "records" / "source.mp4"
            source.parent.mkdir(parents=True)
            original_bytes = b"fixture-video-original"
            source.write_bytes(original_bytes)
            finish = media_root / "Finish"
            finish.mkdir(parents=True)
            moments = [
                plugin_api._create_video_moment(
                    media_root,
                    home,
                    "records/source.mp4",
                    {"timeSeconds": 20.0 + index * 30, "note": f"Moment {index}", "targetFormat": "short"},
                )
                for index in range(2)
            ]
            job = plugin_api._create_render_job(
                media_root,
                home,
                "records/source.mp4",
                {"kind": "best_of", "momentIds": [moment["id"] for moment in moments], "clipDurationSeconds": 18},
            )

            def write_segment(_source: Path, _start: float, _duration: int, output_path: Path) -> None:
                output_path.write_bytes(b"vertical-segment")

            def write_best_of(_segments: list[Path], output_path: Path, _staging: Path) -> None:
                output_path.write_bytes(b"concatenated-best-of")

            with patch("plugin_api._media_root", return_value=media_root), patch(
                "plugin_api._probe_video_duration", return_value=120.0
            ), patch("plugin_api._render_vertical_segment", side_effect=write_segment), patch(
                "plugin_api._concat_rendered_segments", side_effect=write_best_of
            ):
                plugin_api._render_job_worker(home, job["id"])

            saved_job = next(item for item in plugin_api._load_render_jobs(home) if item["id"] == job["id"])
            self.assertEqual(saved_job["status"], "completed")
            self.assertEqual(saved_job["progress"], 2)
            self.assertEqual(len(saved_job["outputFiles"]), 1)
            output = finish / saved_job["outputFiles"][0]
            self.assertEqual(output.read_bytes(), b"concatenated-best-of")
            self.assertEqual(source.read_bytes(), original_bytes)
            inventory = plugin_api._scan_folder(media_root, "finish", "Finish")
            self.assertEqual([video["name"] for video in inventory["videos"]], saved_job["outputFiles"])
            self.assertFalse((finish / ".video-agent-rendering" / job["id"]).exists())

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg tools are not installed")
    def test_ffmpeg_produces_vertical_clips_and_concatenates_a_best_of(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "synthetic-source.mp4"
            staging = root / "staging"
            staging.mkdir()
            subprocess.run(
                [
                    "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-n",
                    "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=30:d=3",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=3",
                    "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=3",
                    "-map", "0:v:0", "-map", "1:a:0", "-map", "2:a:0",
                    "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-movflags", "+faststart", str(source),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=30,
            )
            original_bytes = source.read_bytes()
            first_clip = staging / "segment-000.mp4"
            second_clip = staging / "segment-001.mp4"
            plugin_api._render_vertical_segment(source, 0.0, 2, first_clip)
            plugin_api._render_vertical_segment(source, 1.0, 2, second_clip)
            best_of = staging / "best-of.mp4"
            plugin_api._concat_rendered_segments([first_clip, second_clip], best_of, staging)

            probe = subprocess.run(
                [
                    "ffprobe", "-v", "error", "-show_entries",
                    "stream=codec_type,codec_name,width,height:format=duration", "-of", "json", str(first_clip),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            clip_metadata = json.loads(probe.stdout)
            video_stream = next(stream for stream in clip_metadata["streams"] if stream["codec_type"] == "video")
            audio_streams = [stream for stream in clip_metadata["streams"] if stream["codec_type"] == "audio"]
            clip_duration = float(clip_metadata["format"]["duration"])
            self.assertEqual((video_stream["width"], video_stream["height"]), (1080, 1920))
            self.assertEqual(len(audio_streams), 2, "all source audio tracks must survive vertical rendering")
            self.assertGreaterEqual(clip_duration, 1.8)
            self.assertLessEqual(clip_duration, 2.2)

            best_of_probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type:format=duration", "-of", "json", str(best_of)],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            best_of_metadata = json.loads(best_of_probe.stdout)
            best_of_duration = float(best_of_metadata["format"]["duration"])
            best_of_audio_streams = [stream for stream in best_of_metadata["streams"] if stream["codec_type"] == "audio"]
            self.assertEqual(len(best_of_audio_streams), 2, "the Best Of must retain every source audio track")
            self.assertGreaterEqual(best_of_duration, 3.5)
            self.assertLessEqual(best_of_duration, 4.5)
            self.assertEqual(source.read_bytes(), original_bytes, "rendering must not modify the input video")


if __name__ == "__main__":
    unittest.main()
