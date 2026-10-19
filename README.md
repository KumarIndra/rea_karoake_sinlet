# Singlet

Singlet is a small, original karaoke maker for turning a song into an instrumental
and an isolated-vocal track with the open-source Demucs model. The application,
uploaded audio, and separation run on your own computer. There is no account,
analytics, or third-party audio API.

## Requirements

- Python 3.11 or 3.12
- A first-run internet connection to install the Python packages and fetch the
  Demucs model weights
- About 12 GB of system memory is available on the machine this was prepared
  for. The `htdemucs` model can be memory-intensive; close other applications
  and try a shorter song if the system runs out of memory.
- This setup uses the CPU. Short tracks can still take a few minutes to
  separate; performance depends on your computer.

The model weights are downloaded once by Demucs and are cached in your user
directory. Uploaded songs and outputs are written to a temporary local folder;
they are removed when you choose **Start over** or the app shuts down. A track
can be up to 100 MB; one separation runs at a time and up to two tracks may be
queued or processing.

Demucs downloads its published, hash-checked `htdemucs` checkpoint on first
use. The dependency list pins CPU-only PyTorch and Torchaudio versions that
support Demucs without a separate GPU runtime.

## Run on Windows

Open PowerShell in this project folder:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000> in your browser. The first separation can take
longer while Demucs downloads its model. The app accepts MP3, WAV, FLAC, M4A,
and OGG files. Completed stems are WAV files that you can preview or download.

Keep the server bound to `127.0.0.1`; it is designed as a local application,
not as a public-facing multi-user service.

## Tests

```powershell
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

## Model

Demucs is open-source software by its respective authors. Model-generated
separation is imperfect: some vocal or instrumental bleed may remain. You are
responsible for having permission to process or share the music you select.
