# Stem Splitter

A small local app that splits a song into individual stems using
[Demucs](https://github.com/facebookresearch/demucs) (`htdemucs_6s`), lets you set the
volume of each stem (including mute/solo) in a Streamlit UI, mixes them back at your chosen
levels, and exports the result as a new MP3.

Provide a song either by **uploading an MP3** or by **pasting a YouTube link** — the app
downloads the audio for you (via yt-dlp + FFmpeg), so there's no need to convert it first.

**A note on YouTube audio quality:** YouTube only serves lossy audio (Opus at roughly
130-160 kbps, or AAC around 128 kbps). There is no 320 kbps MP3 or lossless source to
download, so that is the real quality ceiling. To avoid degrading it further, the app grabs
the best available stream and extracts it into a lossless WAV (rather than re-encoding to a
lower-quality MP3) before separating. Uploading a high-quality MP3 or lossless file you
already own will always give the best separation results.

**Stems:** vocals, drums, bass, guitar, piano, other
(there is no separate "noise" stem — anything not captured by the others lands in **other**).

## Requirements

- Windows with PowerShell
- Python 3.12.x with the `py` launcher
- Git
- FFmpeg (install steps below)
- Runs on CPU by default; automatically uses an NVIDIA GPU if one is available.
  Expect a few minutes per song on CPU. The first run also downloads the model
  weights (a few hundred MB), one time.

## Quick start (recommended)

On a fresh machine, run the setup script once from the project root in PowerShell.
It installs FFmpeg, creates the virtual environment, and installs all dependencies
in the correct order:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

After that, just **double-click `run.bat`** to launch the app (it opens in your
browser at http://localhost:8501). That's it.

The manual steps below do the same thing by hand if you'd rather not use the scripts.

## Manual setup

Run these from the project root in PowerShell.

### 1. Install FFmpeg (one time)

```powershell
winget install Gyan.FFmpeg
```

Close and reopen the terminal so the new PATH takes effect, then verify:

```powershell
ffmpeg -version
```

### 2. Create and activate a virtual environment

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If activation is blocked by execution policy, allow it for this session:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

### 3. Install dependencies

PyTorch CPU wheels come from the PyTorch index and must be installed first:

```powershell
pip install --upgrade pip
pip install torch==2.2.2 torchaudio==2.2.2 --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
```

Verify the imports:

```powershell
python -c "import demucs, torch, streamlit, soundfile, numpy; print('ok')"
```

## Run

Double-click **`run.bat`**, or from an activated venv:

```powershell
streamlit run app.py
```

Either way it opens in your browser (usually http://localhost:8501).

## How to use

1. Provide a song, either:
   - **Upload MP3** tab: upload an MP3, or
   - **YouTube link** tab: paste a link and click **Fetch audio from YouTube**.
2. Click **Separate into stems** and wait for Demucs to finish
   (a few minutes on CPU; the first run also downloads the model).
3. Adjust the per-stem sliders (0–100%). Use **Mute** to drop a stem, or **Solo**
   to hear only selected stems.
4. (Optional) Under **Click track**:
   - **Count-in**: prepend a "1, 2, 3, 4" cue before the song.
   - **Metronome**: lay a steady click over the song, with its own volume and a
     start/end window (in 0.1s steps) so you can crop it to just part of the song.
     Clicks are **locked to the song's beats** (the grid is phased to the detected
     first beat), so they land on real beats no matter where you crop. Use the
     **Count-in → drums enter** button to run the metronome from the start until
     the drums come in on the 1, then stop.
   Both share one tempo, estimated from the **first 10 seconds after the audio
   starts** (nudge it if needed). The metronome runs at a **fixed** tempo on
   purpose, so it stays a steady reference for the intro even when a song's tempo
   drifts later (e.g. The Lemon Song).

   If a song has leading silence before the instruments come in, the app detects
   where the audio actually starts and offers **Trim leading silence** (on by
   default), so the count-in leads straight into the first note instead of into a
   silent gap.
5. Click **Build / preview mix** to hear the current mix, then **Download MP3**.

The separated stems are cached per file, so re-mixing only re-encodes when you click
**Build / preview mix** — Demucs runs once per file.

**Example:** paste a YouTube link, keep vocals + drums at 100%, mute guitar and bass, export.

> **Note:** Only download audio you have the right to use. Respect YouTube's Terms of Service
> and copyright law.

## Project layout

```
app.py            # Streamlit app (upload -> separate -> sliders -> mix -> download)
requirements.txt  # pinned, verified dependency versions
data/             # uploaded inputs (gitignored)
separated/        # Demucs stem output cache (gitignored)
output/           # exported mixes (gitignored)
samples/          # a short test MP3
```

## Notes / troubleshooting

- **Shell:** PowerShell uses `;` as the command separator, not `&&`.
- **Model cache:** Demucs weights download once into `models/` inside the project (pinned via
  `TORCH_HOME`) and are reused on every run — no repeat downloads. The folder is gitignored.
- **numpy is pinned `<2`** to stay compatible with the Demucs / soundfile stack.
- **FFmpeg not found** when running the app: reopen your terminal after installing it so the
  updated PATH is picked up.
- **YouTube TLS / certificate errors:** on networks with a TLS-inspecting proxy or security
  tool, Python's bundled certificates won't recognise the intercepting root CA. The app uses
  [`truststore`](https://pypi.org/project/truststore/) to verify against the Windows
  certificate store instead, which fixes `unable to get local issuer certificate` errors.
- **YouTube "page needs to be reloaded" / extraction errors:** YouTube changes frequently.
  The app prefers alternative player clients to stay reliable, but if a download fails, update
  yt-dlp: `pip install -U yt-dlp`.
- **Slow separation** is normal on CPU. If you have a supported NVIDIA GPU with a CUDA-enabled
  PyTorch build, the app will use it automatically.
