"""
Stem Splitter — local Streamlit app.

Provide a song by uploading an MP3 or pasting a YouTube link ->
split into 6 stems with Demucs (htdemucs_6s) ->
adjust per-stem volume (mute/solo) -> mix -> download as MP3.

Separated stems are cached per file so moving a slider only re-mixes;
Demucs runs only once per uploaded file.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Pin the model cache to a folder inside the project, BEFORE importing torch,
# so Demucs always downloads/reads its weights from the same stable location
# regardless of how or from where the app is launched. The weights are a
# one-time download (a few hundred MB) that then persists here across runs.
APP_DIR = Path(__file__).parent.resolve()
MODEL_CACHE_DIR = APP_DIR / "models"
MODEL_CACHE_DIR.mkdir(exist_ok=True)
os.environ.setdefault("TORCH_HOME", str(MODEL_CACHE_DIR))

import librosa
import numpy as np
import soundfile as sf
import streamlit as st
import torch
import truststore
import yt_dlp

# Use the operating system's certificate store for TLS verification.
# This matters on machines behind a TLS-inspecting proxy / security tool
# (common on corporate networks), where the intercepting root CA lives in the
# Windows cert store but NOT in Python's bundled certifi bundle — without this,
# yt-dlp fails with "unable to get local issuer certificate".
truststore.inject_into_ssl()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MODEL_NAME = "htdemucs_6s"
STEMS = ["vocals", "drums", "bass", "guitar", "piano", "other"]

DATA_DIR = APP_DIR / "data"          # uploaded inputs
SEPARATED_DIR = APP_DIR / "separated"  # demucs output
OUTPUT_DIR = APP_DIR / "output"      # exported mixes

for d in (DATA_DIR, SEPARATED_DIR, OUTPUT_DIR):
    d.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def find_ffmpeg() -> str:
    """
    Locate the ffmpeg executable.

    Streamlit may be launched from an environment that doesn't have FFmpeg on
    PATH yet (e.g. a terminal opened before FFmpeg was installed), which causes
    "[WinError 2] The system cannot find the file specified". So we check PATH
    first, then common Windows install locations (winget / Chocolatey).
    """
    found = shutil.which("ffmpeg")
    if found:
        return found

    candidates = []
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        # winget shim + package install dir
        candidates.append(Path(local) / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe")
        pkgs = Path(local) / "Microsoft" / "WinGet" / "Packages"
        if pkgs.exists():
            candidates.extend(pkgs.glob("Gyan.FFmpeg*/**/bin/ffmpeg.exe"))
    candidates.append(Path(r"C:\ProgramData\chocolatey\bin\ffmpeg.exe"))

    for c in candidates:
        if Path(c).exists():
            return str(c)

    raise RuntimeError(
        "FFmpeg was not found. Install it with `winget install Gyan.FFmpeg`, "
        "then close and reopen your terminal before running the app."
    )


# Resolve once at startup. If found via a fallback path, make sure its folder is
# on PATH so child tools (Demucs, yt-dlp) can find it too.
FFMPEG = find_ffmpeg()
_ffmpeg_dir = str(Path(FFMPEG).parent)
if _ffmpeg_dir not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")


def file_hash(raw: bytes) -> str:
    """Stable short hash of the uploaded bytes, used as a cache key."""
    return hashlib.sha1(raw).hexdigest()[:16]


def pick_device() -> str:
    return "cuda" if torch.cuda.is_available() else "cpu"


@st.cache_data(show_spinner=False)
def download_youtube_audio(url: str) -> tuple[bytes, str]:
    """
    Download the best available audio from a YouTube video and return
    (wav_bytes, title). Cached by URL so re-pasting the same link is instant.

    Quality note: YouTube only serves lossy audio (Opus ~130-160 kbps or
    AAC ~128 kbps); there is no 320 kbps MP3 or lossless source to fetch. To
    avoid throwing away *more* quality, we grab the best audio stream and extract
    it into a lossless WAV container rather than re-encoding to MP3. Demucs then
    decodes the WAV directly. This preserves as much of the original as possible.
    """
    with tempfile.TemporaryDirectory() as tmp:
        out_template = str(Path(tmp) / "%(id)s.%(ext)s")
        ydl_opts = {
            # Prefer the highest-bitrate audio stream available.
            "format": "bestaudio/best",
            "outtmpl": out_template,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            # Try multiple player clients for reliability. ios/android tend to be
            # the most robust against YouTube's transient extraction errors, and
            # we fall back to web.
            "extractor_args": {"youtube": {"player_client": ["ios", "web", "android"]}},
            "ffmpeg_location": FFMPEG,
            "postprocessors": [
                {
                    # Lossless container: no second lossy transcode.
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "wav",
                }
            ],
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"Could not download audio from that link:\n{e}") from e

        title = info.get("title", "youtube_audio")
        wav_files = list(Path(tmp).glob("*.wav"))
        if not wav_files:
            raise RuntimeError("Download finished but no audio was produced.")
        return wav_files[0].read_bytes(), title


@st.cache_data(show_spinner=False)
def separate_stems(raw: bytes, key: str) -> dict[str, str]:
    """
    Run Demucs on the uploaded audio and return {stem_name: wav_path}.

    Cached by (raw bytes, key) so re-running with the same file is instant.
    The heavy Demucs run happens exactly once per unique file.
    """
    input_path = DATA_DIR / f"{key}.mp3"
    input_path.write_bytes(raw)

    device = pick_device()

    # Call the demucs CLI in this same interpreter so it uses our venv + torch.
    cmd = [
        sys.executable, "-m", "demucs",
        "-n", MODEL_NAME,
        "-d", device,
        "-o", str(SEPARATED_DIR),
        str(input_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            "Demucs separation failed.\n\n"
            f"stdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"
        )

    # Demucs writes to: separated/<model>/<input_stem>/<stem>.wav
    stem_dir = SEPARATED_DIR / MODEL_NAME / input_path.stem
    result: dict[str, str] = {}
    for stem in STEMS:
        wav = stem_dir / f"{stem}.wav"
        if wav.exists():
            result[stem] = str(wav)
    if not result:
        raise RuntimeError(f"No stems found in {stem_dir}")
    return result


@st.cache_data(show_spinner=False)
def load_stem(path: str) -> tuple[np.ndarray, int]:
    """Load a stem WAV as float32 (frames, channels)."""
    data, sr = sf.read(path, dtype="float32", always_2d=True)
    return data, sr


@st.cache_data(show_spinner=False)
def stem_duration(path: str) -> float:
    """Duration of a stem in seconds (stems all share the song's length)."""
    info = sf.info(path)
    return float(info.duration)


def _first_audio_onset(y: np.ndarray, sr: int, peak_fraction: float = 0.1) -> float:
    """
    Time (s) where audio meaningfully starts, i.e. the first frame whose energy
    exceeds `peak_fraction` of the signal's peak. Used to skip leading silence
    so the click grid isn't thrown off by a silent intro.
    """
    if y.size == 0:
        return 0.0
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=512)[0]
    if not rms.size:
        return 0.0
    thresh = peak_fraction * float(np.max(rms))
    loud = np.where(rms > thresh)[0]
    if not loud.size:
        return 0.0
    return float(librosa.frames_to_time(loud[0], sr=sr, hop_length=512))


@st.cache_data(show_spinner=False)
def analyze_song(stem_paths: dict[str, str], tempo_window: float = 10.0) -> dict:
    """
    Analyse the song for click-track timing. Returns a dict with:
      - bpm:         tempo estimated from the first `tempo_window` seconds *after*
                     the audio actually starts. Older songs drift, but we only
                     need the opening tempo to hold until the parts come in.
      - audio_start: time (s) the song's audio begins (first non-silent point
                     across all stems). The click grid is phased to this so a
                     silent intro doesn't knock the count-in/metronome off.
      - first_beat:  grid phase (s) for the metronome, aligned to a real beat at
                     or after audio_start.
      - drums_in:    time (s) the drums enter (snapped to a beat), for the
                     "metronome until the drums come in" convenience.
    """
    # 1. Find where the whole song starts (any instrument), from a quick full mix.
    full = None
    sr = 22050
    for path in stem_paths.values():
        y, sr = librosa.load(path, sr=sr, mono=True)
        full = y if full is None else full[: min(len(full), len(y))] + y[: min(len(full), len(y))]
    if full is None or full.size == 0:
        return {"bpm": 120.0, "audio_start": 0.0, "first_beat": 0.0, "drums_in": 0.0}

    audio_start = _first_audio_onset(full, sr)

    # 2. Tempo from a window that begins at the audio start (opening tempo only).
    start_sample = int(audio_start * sr)
    win_samples = int(tempo_window * sr)
    tempo_src = stem_paths.get("drums")
    yd, _ = librosa.load(tempo_src, sr=sr, mono=True) if tempo_src else (full, sr)
    segment = yd[start_sample:start_sample + win_samples]
    if segment.size < sr:  # too short; fall back to the full-mix opening
        segment = full[start_sample:start_sample + win_samples]

    bpm = 120.0
    beat_times = np.array([])
    if segment.size >= sr:
        tempo, beat_frames = librosa.beat.beat_track(y=segment, sr=sr)
        b = float(np.atleast_1d(tempo)[0])
        if np.isfinite(b) and b > 0:
            bpm = b
        # Beat times are relative to the segment; shift back to absolute time.
        beat_times = librosa.frames_to_time(beat_frames, sr=sr) + audio_start

    # 3. Grid phase: first detected beat at/after the audio start, else audio_start.
    if beat_times.size:
        first_beat = float(beat_times[0])
    else:
        first_beat = audio_start

    # 4. Where the drums enter (snapped to a beat), for the convenience button.
    drums_in = audio_start
    if tempo_src:
        yfull, _ = librosa.load(tempo_src, sr=sr, mono=True)
        d = _first_audio_onset(yfull, sr, peak_fraction=0.15)
        if beat_times.size:
            d = float(beat_times[np.argmin(np.abs(beat_times - d))])
        drums_in = d

    return {
        "bpm": round(bpm, 1),
        "audio_start": round(audio_start, 3),
        "first_beat": round(first_beat, 3),
        "drums_in": round(drums_in, 3),
    }


def _click_blip(sr: int, freq: float, gain: float = 0.6) -> np.ndarray:
    """A single short decaying sine 'tick' (mono, float32)."""
    click_len = int(0.06 * sr)  # 60 ms
    t = np.arange(click_len) / sr
    envelope = np.exp(-t * 30.0).astype("float32")  # fast decay
    return (gain * envelope * np.sin(2 * np.pi * freq * t)).astype("float32")


def _clicks_on_grid(
    total_frames: int,
    bpm: float,
    sr: int,
    accent_every: int | None = None,
    offset_frames: int = 0,
) -> np.ndarray:
    """
    Lay clicks on a fixed beat grid over a mono buffer of `total_frames`.

    Every beat gets a click; if `accent_every` is set, every Nth beat is pitched
    higher (e.g. accent_every=4 accents beat 1 of each bar). `offset_frames`
    shifts where the first beat lands. Fixed tempo by design — it does not follow
    tempo drift in the song.
    """
    out = np.zeros(total_frames, dtype="float32")
    beat_len = int(round((60.0 / bpm) * sr))
    if beat_len <= 0:
        return out
    normal = _click_blip(sr, 1000.0)
    accent = _click_blip(sr, 1500.0)

    beat_idx = 0
    pos = offset_frames
    while pos < total_frames:
        blip = accent if (accent_every and beat_idx % accent_every == 0) else normal
        end = min(pos + blip.shape[0], total_frames)
        if pos >= 0:
            out[pos:end] += blip[: end - pos]
        pos += beat_len
        beat_idx += 1
    return out


def make_count_in(bpm: float, sr: int, beats: int = 4) -> np.ndarray:
    """
    A `beats`-beat count-in click at `bpm`. Stereo, float32, (frames, 2).
    The first beat is accented (higher pitched) so "1" is easy to find.
    """
    total = int(round((60.0 / bpm) * sr)) * beats
    mono = _clicks_on_grid(total, bpm, sr, accent_every=beats)
    return np.stack([mono, mono], axis=1)


def make_metronome(
    song_frames: int,
    bpm: float,
    sr: int,
    first_beat: float = 0.0,
    start: float = 0.0,
    end: float | None = None,
    accent_every: int = 4,
) -> np.ndarray:
    """
    A fixed-tempo metronome laid on a beat grid phased to `first_beat` (seconds),
    so clicks land on the song's real beats rather than on t=0. Clicks are only
    emitted between `start` and `end` seconds (end None = end of song), but the
    grid itself is global, so cropping never shifts the click timing.

    Returns stereo float32 of length `song_frames`. Fixed tempo on purpose: for
    songs whose tempo drifts, this stays a steady reference at the detected BPM.
    """
    out = np.zeros(song_frames, dtype="float32")
    beat_len = int(round((60.0 / bpm) * sr))
    if beat_len <= 0:
        return np.stack([out, out], axis=1)

    normal = _click_blip(sr, 1000.0)
    accent = _click_blip(sr, 1500.0)

    end_frame = song_frames if end is None else min(song_frames, int(round(end * sr)))
    start_frame = max(0, int(round(start * sr)))
    phase = int(round(first_beat * sr))

    # Walk the global beat grid; emit only clicks whose onset is in [start, end).
    # Track beat index from the phase so accents stay on the bar's "1".
    first_idx = int(np.floor((start_frame - phase) / beat_len)) if beat_len else 0
    beat_idx = max(0, first_idx)
    pos = phase + beat_idx * beat_len
    while pos < end_frame:
        if pos >= start_frame and pos >= 0:
            blip = accent if (accent_every and beat_idx % accent_every == 0) else normal
            e = min(pos + blip.shape[0], song_frames)
            out[pos:e] += blip[: e - pos]
        pos += beat_len
        beat_idx += 1

    return np.stack([out, out], axis=1)


def mix_stems(
    stem_paths: dict[str, str],
    volumes: dict[str, float],
    count_in: bool = False,
    bpm: float | None = None,
    metronome: bool = False,
    metronome_volume: float = 0.5,
    metronome_start: float = 0.0,
    metronome_end: float | None = None,
    metronome_first_beat: float = 0.0,
    audio_start: float = 0.0,
    trim_lead_silence: bool = False,
) -> tuple[np.ndarray, int]:
    """
    Sum stems at the given linear volumes (0.0-1.0). Returns (audio, sr).

    If `count_in` is True, a 4-beat click at `bpm` is prepended so you get a
    "1, 2, 3, 4" cue before the song starts.

    If `metronome` is True, a fixed-tempo click at `bpm` is layered over the song
    between `metronome_start` and `metronome_end` seconds (end None = to the end
    of the song), on a beat grid phased to `metronome_first_beat` so clicks land
    on the song's real beats. Fixed tempo by design, so it stays a steady
    reference even when the song's own tempo drifts.
    """
    mixed = None
    sr = None
    for stem, path in stem_paths.items():
        vol = volumes.get(stem, 1.0)
        if vol <= 0:
            continue
        data, this_sr = load_stem(path)
        sr = this_sr if sr is None else sr
        scaled = data * vol
        mixed = scaled if mixed is None else mixed + scaled
    if mixed is None:
        # Everything muted: produce a short silent buffer matching the song length
        # if we can determine it, else a brief placeholder.
        sr = sr or 44100
        any_stem = next(iter(stem_paths.values()), None)
        if any_stem:
            data, sr = load_stem(any_stem)
            mixed = np.zeros_like(data)
        else:
            mixed = np.zeros((sr, 2), dtype="float32")
    # Guard against clipping.
    peak = float(np.max(np.abs(mixed))) if mixed.size else 0.0
    if peak > 1.0:
        mixed = mixed / peak

    # Optionally trim leading silence so the count-in flows straight into the
    # first real audio instead of into a silent gap. Everything after this point
    # is shifted earlier by `audio_start`, so beat/window times shift with it.
    time_shift = 0.0
    if trim_lead_silence and audio_start > 0:
        cut = min(int(round(audio_start * sr)), mixed.shape[0])
        mixed = mixed[cut:]
        time_shift = audio_start

    # Layer the metronome over the song within the chosen window. The click grid
    # is phased to the song's first beat so clicks land on real beats, and the
    # window crops which clicks sound without shifting their timing. Times are
    # adjusted for any leading-silence trim.
    if metronome and bpm and metronome_volume > 0:
        m_end = None if metronome_end is None else max(0.0, metronome_end - time_shift)
        click = make_metronome(
            mixed.shape[0], bpm, sr,
            first_beat=max(0.0, metronome_first_beat - time_shift),
            start=max(0.0, metronome_start - time_shift),
            end=m_end,
        ) * metronome_volume
        mixed = mixed.copy() + click
        peak = float(np.max(np.abs(mixed)))
        if peak > 1.0:
            mixed = mixed / peak

    # Prepend the count-in (after the metronome so the count-in stays clean).
    if count_in and bpm:
        click = make_count_in(bpm, sr)
        mixed = np.concatenate([click, mixed], axis=0)

    return mixed, sr


def encode_mp3(audio: np.ndarray, sr: int) -> bytes:
    """Encode float audio to MP3 bytes via FFmpeg (WAV -> MP3 through a pipe)."""
    wav_buf = io.BytesIO()
    sf.write(wav_buf, audio, sr, format="WAV", subtype="PCM_16")
    wav_buf.seek(0)

    cmd = [
        FFMPEG, "-hide_banner", "-loglevel", "error",
        "-y", "-i", "pipe:0",
        "-codec:a", "libmp3lame", "-b:a", "320k",
        "-f", "mp3", "pipe:1",
    ]
    proc = subprocess.run(cmd, input=wav_buf.read(), capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg encode failed:\n{proc.stderr.decode(errors='replace')}")
    return proc.stdout


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Stem Splitter", page_icon="🎚️", layout="centered")
st.title("🎚️ Stem Splitter")
st.caption(
    f"Split an MP3 into {len(STEMS)} stems with Demucs ({MODEL_NAME}), "
    "adjust each level, and export a new mix."
)

# Two ways to provide a song: upload a file, or paste a YouTube link.
raw: bytes | None = None
display_name: str | None = None
source_mime: str = "audio/mp3"

tab_upload, tab_youtube = st.tabs(["Upload MP3", "YouTube link"])

with tab_upload:
    uploaded = st.file_uploader("Upload an MP3", type=["mp3"])
    if uploaded is not None:
        raw = uploaded.getvalue()
        display_name = uploaded.name
        source_mime = "audio/mp3"

with tab_youtube:
    yt_url = st.text_input("Paste a YouTube link", placeholder="https://www.youtube.com/watch?v=…")
    st.caption("YouTube audio is lossy at the source (~128-160 kbps). "
               "We fetch the best stream available and keep it lossless (WAV) for separation.")
    if st.button("Fetch audio from YouTube"):
        if not yt_url.strip():
            st.warning("Paste a link first.")
        else:
            with st.spinner("Downloading audio from YouTube…"):
                try:
                    yt_bytes, yt_title = download_youtube_audio(yt_url.strip())
                    # Stash so it survives reruns triggered by later widgets.
                    st.session_state["yt_audio"] = (yt_bytes, f"{yt_title}.wav")
                    st.success(f"Fetched: {yt_title}")
                except Exception as e:  # noqa: BLE001
                    st.error(str(e))
    if "yt_audio" in st.session_state:
        raw, display_name = st.session_state["yt_audio"]
        source_mime = "audio/wav"

if raw is not None:
    key = file_hash(raw)

    st.audio(raw, format=source_mime)

    if st.button("Separate into stems", type="primary"):
        with st.spinner("Running Demucs… first run also downloads model weights (a few hundred MB)."):
            try:
                st.session_state[f"stems_{key}"] = separate_stems(raw, key)
            except Exception as e:  # noqa: BLE001
                st.error(str(e))

    stem_paths = st.session_state.get(f"stems_{key}")
    if stem_paths:
        st.success("Stems ready. Adjust the levels, preview, then export.")

        st.subheader("Stem levels")
        solo = st.multiselect(
            "Solo (if any selected, only these play)",
            options=list(stem_paths.keys()),
        )

        volumes: dict[str, float] = {}
        for stem in stem_paths:
            col1, col2 = st.columns([1, 4])
            with col1:
                muted = st.checkbox("Mute", key=f"mute_{key}_{stem}")
            with col2:
                pct = st.slider(
                    stem.capitalize(),
                    min_value=0, max_value=100, value=100,
                    key=f"vol_{key}_{stem}",
                )
            active = (not muted) and (not solo or stem in solo)
            volumes[stem] = (pct / 100.0) if active else 0.0

        st.divider()
        st.subheader("Click track")

        count_in = st.checkbox(
            "Add a 4-beat count-in click before the song",
            help="Prepends a '1, 2, 3, 4' click at the tempo below so you can cue in.",
        )
        metronome = st.checkbox(
            "Add a metronome over the song",
            help="Lays a steady click over the song at the tempo below. Fixed tempo, so "
                 "it stays a consistent reference even if the song's own tempo drifts.",
        )

        # Both features share a single tempo, taken from the opening of the song
        # (first 10s after the audio starts) so it holds until the parts come in.
        bpm: float | None = None
        metronome_volume = 0.5
        metronome_start = 0.0
        metronome_end: float | None = None
        first_beat = 0.0
        drums_in = 0.0
        audio_start = 0.0
        trim_lead_silence = False
        duration = stem_duration(next(iter(stem_paths.values())))

        if count_in or metronome:
            with st.spinner("Analysing the opening for tempo and timing…"):
                analysis = analyze_song(stem_paths)
            detected = analysis["bpm"]
            first_beat = analysis["first_beat"]
            drums_in = analysis["drums_in"]
            audio_start = analysis["audio_start"]
            bpm = st.number_input(
                "Tempo (BPM)", min_value=40.0, max_value=240.0,
                value=float(detected), step=0.5,
                help="Estimated from the first 10 seconds after the audio starts. "
                     "Nudge it if the click feels off.",
            )
            st.caption(f"Opening tempo: {detected} BPM · audio starts at "
                       f"{audio_start:.2f}s · first beat {first_beat:.2f}s · "
                       f"drums enter around {drums_in:.2f}s")
            if audio_start > 0.05:
                trim_lead_silence = st.checkbox(
                    "Trim leading silence so the count-in leads straight into the song",
                    value=True,
                    help=f"The song has ~{audio_start:.2f}s of near-silence before the audio "
                         "starts. Trimming it keeps the count-in on time.",
                )

        if metronome:
            metronome_volume = st.slider(
                "Metronome volume", min_value=0, max_value=100, value=50,
                help="How loud the metronome sits over the song.",
            ) / 100.0

            # Window state lives in session so the convenience buttons can set it.
            win_key = f"metro_win_{key}"
            if win_key not in st.session_state:
                st.session_state[win_key] = (0.0, round(duration, 1))

            c1, c2 = st.columns(2)
            with c1:
                if st.button("Count-in → drums enter", help="Metronome from the start until "
                             "the drums come in on the 1, then stop."):
                    st.session_state[win_key] = (0.0, round(drums_in, 1) if drums_in > 0 else round(duration, 1))
            with c2:
                if st.button("Whole song"):
                    st.session_state[win_key] = (0.0, round(duration, 1))

            win = st.slider(
                "Metronome active between (seconds)",
                min_value=0.0, max_value=round(duration, 1),
                value=st.session_state[win_key], step=0.1,
                key=f"metro_slider_{key}",
                help="Crop the metronome to a section. Clicks stay locked to the song's "
                     "beats regardless of where you crop.",
            )
            metronome_start, metronome_end = float(win[0]), float(win[1])
            st.caption(f"Song length: {duration:.1f}s · metronome plays "
                       f"{metronome_start:.1f}s–{metronome_end:.1f}s, "
                       f"clicks locked to beats (first beat {first_beat:.2f}s)")

        st.divider()
        st.subheader("Preview & export")
        st.caption("Build a mix at the current levels, preview it, then download when you're happy.")

        # Signature of the current settings, so we can tell when a built mix is
        # out of date relative to the sliders/mutes/click-track options.
        settings_sig = (
            tuple(round(volumes[s], 3) for s in stem_paths),
            bool(count_in),
            bool(metronome),
            round(bpm, 2) if bpm else None,
            round(metronome_volume, 3),
            round(metronome_start, 2),
            round(metronome_end, 2) if metronome_end is not None else None,
            round(first_beat, 3),
            round(audio_start, 3),
            bool(trim_lead_silence),
        )
        preview_key = f"preview_{key}"

        if st.button("Build / preview mix", type="primary"):
            with st.spinner("Mixing and encoding…"):
                try:
                    audio, sr = mix_stems(
                        stem_paths, volumes,
                        count_in=count_in, bpm=bpm,
                        metronome=metronome, metronome_volume=metronome_volume,
                        metronome_start=metronome_start, metronome_end=metronome_end,
                        metronome_first_beat=first_beat,
                        audio_start=audio_start, trim_lead_silence=trim_lead_silence,
                    )
                    mix_mp3 = encode_mp3(audio, sr)
                    st.session_state[preview_key] = {"sig": settings_sig, "mp3": mix_mp3}
                except Exception as e:  # noqa: BLE001
                    st.error(str(e))

        preview = st.session_state.get(preview_key)
        if preview:
            if preview["sig"] != settings_sig:
                st.warning("Levels changed since this mix was built. "
                           "Click **Build / preview mix** to update it.")
            else:
                active_stems = [s for s, v in volumes.items() if v > 0]
                st.caption("This mix contains: "
                           + (", ".join(active_stems) if active_stems else "nothing (all muted)"))

            st.audio(preview["mp3"], format="audio/mp3")

            safe_stem = re.sub(r'[\\/:*?"<>|]', "_", Path(display_name).stem).strip() or "mix"
            out_name = f"{safe_stem}_mix.mp3"

            def _save_mix(data: bytes = preview["mp3"], name: str = out_name) -> None:
                (OUTPUT_DIR / name).write_bytes(data)

            st.download_button(
                "Download MP3",
                data=preview["mp3"],
                file_name=out_name,
                mime="audio/mpeg",
                on_click=_save_mix,
            )
else:
    st.write("Upload a file or paste a YouTube link to get started.")
