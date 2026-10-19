import asyncio
import logging
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import imageio_ffmpeg
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_JOBS = 2
logger = logging.getLogger("singlet")
JOBS: dict[str, dict] = {}
SEMAPHORE = asyncio.Semaphore(1)
WORK_ROOT = Path(tempfile.gettempdir()) / "karaoke-maker"
WORK_ROOT.mkdir(exist_ok=True)

app = FastAPI(title="Karaoke Maker")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
async def home():
    return FileResponse(ROOT / "static" / "index.html")


def active_jobs() -> int:
    return sum(job["status"] in {"queued", "processing"} for job in JOBS.values())


def purge_finished_jobs() -> None:
    for job_id, job in list(JOBS.items()):
        if job["status"] in {"completed", "failed"}:
            shutil.rmtree(job["directory"], ignore_errors=True)
            del JOBS[job_id]


async def process_audio(job_id: str) -> None:
    job = JOBS[job_id]
    async with SEMAPHORE:
        job["status"] = "processing"
        job["message"] = "Preparing your track for local AI separation"
        normalized = job["directory"] / "normalized.wav"
        try:
            ffmpeg = await asyncio.to_thread(imageio_ffmpeg.get_ffmpeg_exe)
            convert_command = [
                ffmpeg,
                "-y",
                "-i",
                str(job["input"]),
                "-vn",
                "-ac",
                "2",
                "-ar",
                "44100",
                "-c:a",
                "pcm_s16le",
                str(normalized),
            ]
            conversion = await asyncio.to_thread(
                subprocess.run,
                convert_command,
                capture_output=True,
                text=True,
                timeout=60 * 30,
                check=False,
            )
            if conversion.returncode != 0 or not normalized.is_file():
                details = (
                    conversion.stderr or conversion.stdout or "FFmpeg could not read this audio file."
                )[-2000:]
                raise RuntimeError(details)

            job["message"] = "Separating vocals from the instrumental"
            command = [
                sys.executable,
                "-m",
                "demucs",
                "--two-stems=vocals",
                "-n",
                "htdemucs",
                "--out",
                str(job["directory"] / "output"),
                str(normalized),
            ]
            result = await asyncio.to_thread(
                subprocess.run,
                command,
                capture_output=True,
                text=True,
                timeout=60 * 60 * 6,
                check=False,
            )
            vocals = job["directory"] / "output" / "htdemucs" / normalized.stem / "vocals.wav"
            instrumental = (
                job["directory"] / "output" / "htdemucs" / normalized.stem / "no_vocals.wav"
            )
            if result.returncode != 0:
                details = (result.stderr or result.stdout or "Demucs exited without an error message.")[-2000:]
                raise RuntimeError(details)
            if not vocals.is_file() or not instrumental.is_file():
                raise RuntimeError("The model finished, but its expected audio stems were not created.")
            job["files"] = {"vocals": vocals, "instrumental": instrumental}
            job["status"] = "completed"
            job["message"] = "Your karaoke and vocal tracks are ready"
            job["input"].unlink(missing_ok=True)
            normalized.unlink(missing_ok=True)
        except subprocess.TimeoutExpired:
            job["status"] = "failed"
            if job["message"] == "Preparing your track for local AI separation":
                job["message"] = "Audio conversion timed out. Try another audio format or shorter track."
            else:
                job["message"] = "Separation timed out. Try a shorter track."
            normalized.unlink(missing_ok=True)
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            job["status"] = "failed"
            job["message"] = str(error)
            logger.exception("Local audio processing failed for job %s", job_id)
            normalized.unlink(missing_ok=True)
        finally:
            job.pop("input", None)


@app.post("/api/jobs", status_code=202)
async def create_job(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    filename = Path(file.filename or "").name
    extension = Path(filename).suffix.lower()
    if extension not in {".mp3", ".wav", ".flac", ".m4a", ".ogg"}:
        raise HTTPException(415, "Choose an MP3, WAV, FLAC, M4A, or OGG audio file.")
    purge_finished_jobs()
    if active_jobs() >= MAX_JOBS:
        raise HTTPException(429, "The local processor is busy. Wait for a track to finish.")

    job_id = uuid.uuid4().hex
    directory = WORK_ROOT / job_id
    directory.mkdir()
    stem = Path(filename).stem[:100] or "track"
    source = directory / f"source{extension}"
    total = 0
    try:
        with source.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "The maximum track size is 100 MB.")
                output.write(chunk)
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    finally:
        await file.close()

    if total == 0:
        shutil.rmtree(directory, ignore_errors=True)
        raise HTTPException(400, "The uploaded audio file is empty.")

    JOBS[job_id] = {
        "status": "queued",
        "message": "Waiting for the local model",
        "name": stem,
        "directory": directory,
        "input": source,
        "stem": source.stem,
        "files": {},
    }
    background_tasks.add_task(process_audio, job_id)
    return {"id": job_id, "name": stem, "status": "queued"}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "This track could not be found. Upload it again to start a new separation.")
    return {
        "id": job_id,
        "name": job["name"],
        "status": job["status"],
        "message": job["message"],
        "tracks": list(job["files"]),
    }


@app.get("/api/jobs/{job_id}/tracks/{track}")
async def get_track(job_id: str, track: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "This track could not be found.")
    if job["status"] != "completed":
        raise HTTPException(409, "This track is not ready yet.")
    if track not in job["files"]:
        raise HTTPException(404, "That audio track is not available.")
    label = "Karaoke instrumental" if track == "instrumental" else "Isolated vocals"
    return FileResponse(
        job["files"][track],
        media_type="audio/wav",
        filename=f"{job['name']}-{track}.wav",
        content_disposition_type="inline",
        headers={"X-Track-Label": label},
    )


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "This track could not be found.")
    if job["status"] in {"queued", "processing"}:
        raise HTTPException(409, "Wait for this track to finish before clearing it.")
    shutil.rmtree(job["directory"], ignore_errors=True)
    del JOBS[job_id]
    return {"deleted": True}


@app.on_event("shutdown")
async def remove_temporary_audio():
    for job in list(JOBS.values()):
        shutil.rmtree(job["directory"], ignore_errors=True)
