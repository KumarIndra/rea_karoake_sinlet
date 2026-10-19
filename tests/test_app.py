import asyncio
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main


class AudioApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self.existing_workdirs = set(main.WORK_ROOT.iterdir())

    def tearDown(self):
        for job in list(main.JOBS.values()):
            shutil.rmtree(job["directory"], ignore_errors=True)
        main.JOBS.clear()

    def test_homepage_and_static_assets_are_served(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertIn("Keep the music", self.client.get("/").text)
        self.assertEqual(self.client.get("/static/app.js").status_code, 200)
        self.assertEqual(self.client.get("/static/styles.css").status_code, 200)

    def test_rejects_unsupported_and_empty_uploads(self):
        unsupported = self.client.post(
            "/api/jobs", files={"file": ("track.txt", b"audio", "text/plain")}
        )
        self.assertEqual(unsupported.status_code, 415)
        empty = self.client.post(
            "/api/jobs", files={"file": ("track.wav", b"", "audio/wav")}
        )
        self.assertEqual(empty.status_code, 400)

    def test_upload_queues_and_returns_clear_unknown_job_error(self):
        with patch("app.main.BackgroundTasks.add_task") as add_task:
            response = self.client.post(
                "/api/jobs", files={"file": ("my-song.wav", b"fake audio", "audio/wav")}
            )
        add_task.assert_called_once()
        self.assertEqual(response.status_code, 202)
        job = self.client.get(f"/api/jobs/{response.json()['id']}")
        self.assertEqual(job.json()["status"], "queued")
        missing = self.client.get("/api/jobs/not-a-job")
        self.assertEqual(missing.status_code, 404)
        self.assertIn("upload it again", missing.json()["detail"].lower())

    def test_serves_generated_stems_as_playable_and_downloadable_wav(self):
        with patch("app.main.BackgroundTasks.add_task"):
            response = self.client.post(
                "/api/jobs", files={"file": ("my-song.wav", b"fake audio", "audio/wav")}
            )
        job_id = response.json()["id"]
        job = main.JOBS[job_id]
        output = job["directory"] / "output" / "htdemucs" / job["stem"]
        output.mkdir(parents=True)
        vocals = output / "vocals.wav"
        instrumental = output / "no_vocals.wav"
        vocals.write_bytes(b"vocal wav")
        instrumental.write_bytes(b"instrumental wav")
        job["status"] = "completed"
        job["files"] = {"vocals": vocals, "instrumental": instrumental}

        response = self.client.get(f"/api/jobs/{job_id}/tracks/instrumental")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"instrumental wav")
        self.assertIn("my-song-instrumental.wav", response.headers["content-disposition"])
        self.assertEqual(
            self.client.get(f"/api/jobs/{job_id}/tracks/vocals").content,
            b"vocal wav",
        )

    def test_local_worker_converts_audio_then_returns_both_demucs_stems(self):
        directory = main.WORK_ROOT / "unit-test-audio-job"
        directory.mkdir()
        source = directory / "source.mp3"
        source.write_bytes(b"audio")
        main.JOBS["unit-test-audio-job"] = {
            "status": "queued",
            "message": "Waiting",
            "name": "song",
            "directory": directory,
            "input": source,
            "stem": "source",
            "files": {},
        }

        def fake_run(command, **_kwargs):
            if command[0].endswith("ffmpeg.exe"):
                Path(command[-1]).write_bytes(b"normalized wav")
            else:
                output = Path(command[command.index("--out") + 1]) / "htdemucs" / "normalized"
                output.mkdir(parents=True)
                (output / "vocals.wav").write_bytes(b"vocal stem")
                (output / "no_vocals.wav").write_bytes(b"instrumental stem")
            return unittest.mock.Mock(returncode=0, stdout="", stderr="")

        with patch("app.main.imageio_ffmpeg.get_ffmpeg_exe", return_value="ffmpeg.exe"):
            with patch("app.main.subprocess.run", side_effect=fake_run):
                asyncio.run(main.process_audio("unit-test-audio-job"))

        job = main.JOBS["unit-test-audio-job"]
        self.assertEqual(job["status"], "completed")
        self.assertEqual(set(job["files"]), {"vocals", "instrumental"})
        self.assertFalse(source.exists())
        self.assertFalse((directory / "normalized.wav").exists())
        self.assertEqual(job["files"]["instrumental"].read_bytes(), b"instrumental stem")

    def test_completed_job_can_be_cleared_and_active_job_cannot(self):
        with patch("app.main.BackgroundTasks.add_task"):
            response = self.client.post(
                "/api/jobs", files={"file": ("my-song.wav", b"fake audio", "audio/wav")}
            )
        job_id = response.json()["id"]
        self.assertEqual(self.client.delete(f"/api/jobs/{job_id}").status_code, 409)
        main.JOBS[job_id]["status"] = "completed"
        cleared = self.client.delete(f"/api/jobs/{job_id}")
        self.assertEqual(cleared.json(), {"deleted": True})
        self.assertEqual(self.client.delete(f"/api/jobs/{job_id}").status_code, 404)

    def test_new_upload_purges_stems_abandoned_after_a_browser_reload(self):
        with patch("app.main.BackgroundTasks.add_task"):
            previous = self.client.post(
                "/api/jobs", files={"file": ("first.wav", b"first", "audio/wav")}
            )
        previous_job_id = previous.json()["id"]
        previous_dir = main.JOBS[previous_job_id]["directory"]
        main.JOBS[previous_job_id]["status"] = "completed"

        with patch("app.main.BackgroundTasks.add_task"):
            current = self.client.post(
                "/api/jobs", files={"file": ("second.wav", b"second", "audio/wav")}
            )

        self.assertEqual(current.status_code, 202)
        self.assertNotIn(previous_job_id, main.JOBS)
        self.assertFalse(previous_dir.exists())

    def test_oversized_upload_cleans_up_partial_file(self):
        chunk = b"x" * (1024 * 1024)
        chunks = (chunk for _ in range(main.MAX_UPLOAD_BYTES // len(chunk) + 1))

        class LargeUpload:
            filename = "large.wav"

            def __init__(self, chunks):
                self.chunks = iter(chunks)

            async def read(self, size):
                return next(self.chunks, b"")

            async def close(self):
                pass

        upload = LargeUpload(chunks)

        async def endpoint():
            return await main.create_job(None, upload)

        with patch.object(main, "MAX_UPLOAD_BYTES", 10):
            with self.assertRaises(Exception) as caught:
                asyncio.run(endpoint())
        self.assertEqual(getattr(caught.exception, "status_code", None), 413)
        self.assertEqual(set(main.WORK_ROOT.iterdir()), self.existing_workdirs)


if __name__ == "__main__":
    unittest.main()
