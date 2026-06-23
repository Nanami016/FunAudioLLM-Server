"""
SenseVoice ASR HTTP Server
--------------------------
Wraps the llama-funasr-sensevoice binary as a FastAPI HTTP service.

Endpoints:
  POST /asr                          - Transcribe audio (file upload or URL)
  POST /v1/audio/transcriptions      - OpenAI Whisper-compatible endpoint
  GET  /health                       - Health check
  GET  /audio/test_silence.wav       - Download test audio
"""

import subprocess
import tempfile
import uuid
import os
import time
import logging
from pathlib import Path
from typing import Optional

import requests
import uvicorn
from fastapi import FastAPI, File, UploadFile, Query, HTTPException, Form
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel

# ─── Logging ───────────────────────────────────────────────────────────────────
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)
log_file = LOG_DIR / f"asr_server_{time.strftime('%Y%m%d')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# ─── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.resolve()
BIN_PATH = BASE_DIR / "bin" / "llama-funasr-sensevoice"
MODEL_PATH = BASE_DIR / "SenseVoiceSmall-GGUF" / "sensevoice-small-q8.gguf"
VAD_PATH = BASE_DIR / "models" / "fsmn-vad.gguf"

for p in [BIN_PATH, MODEL_PATH, VAD_PATH]:
    if not p.exists():
        logger.error(f"Required file not found: {p}")
        raise FileNotFoundError(f"Required file not found: {p}")

# ─── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="SenseVoice ASR Server",
    description="Speech-to-Text service powered by SenseVoiceSmall-GGUF",
    version="1.0.0",
)


class ASRResponse(BaseModel):
    text: str
    duration: Optional[float] = None
    processing_time: float
    language: Optional[str] = None
    error: Optional[str] = None


def run_sensevoice(audio_path: str, keep_tags: bool = False, vad_maxseg: Optional[int] = None) -> dict:
    """Run llama-funasr-sensevoice binary and parse output."""
    cmd = [
        str(BIN_PATH),
        "-m", str(MODEL_PATH),
        "--vad", str(VAD_PATH),
        "-a", audio_path,
    ]
    if keep_tags:
        cmd.append("--keep-tags")
    if vad_maxseg is not None:
        cmd.extend(["--vad-maxseg", str(vad_maxseg)])

    logger.info(f"Running: {' '.join(cmd)}")
    start = time.time()

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        return {"text": "", "error": "Transcription timed out (300s)"}
    except Exception as e:
        return {"text": "", "error": str(e)}

    elapsed = time.time() - start
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()

    if result.returncode != 0:
        logger.error(f"Binary failed (rc={result.returncode}): {stderr}")
        return {"text": "", "error": stderr or "Binary execution failed"}

    logger.info(f"Binary output:\n{stdout}")
    logger.info(f"Completed in {elapsed:.2f}s")

    # Parse output: lines with "[sensevoice] ..." are metadata, others are transcription
    text_lines = []
    language = None
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("[sensevoice]"):
            # Extract language tag if present (e.g. <|zh|>, <|en|>, <|ja|>)
            if "<|" in line and "|>" in line:
                try:
                    lang_start = line.index("<|") + 2
                    lang_end = line.index("|>")
                    lang = line[lang_start:lang_end]
                    if len(lang) <= 5:
                        language = lang
                except ValueError:
                    pass
            continue
        if line and not line.startswith("["):
            text_lines.append(line)

    text = "\n".join(text_lines).strip()

    # If no text from parsed lines, try the full stdout (some versions output differently)
    if not text and stdout:
        # Filter out metadata lines
        content_lines = [l for l in stdout.splitlines() if not l.strip().startswith("[sensevoice]")]
        text = "\n".join(content_lines).strip()

    return {"text": text, "duration": elapsed, "language": language}


def convert_audio(input_path: str, output_path: str, sample_rate: int = 16000) -> bool:
    """Convert audio to 16kHz mono WAV using ffmpeg."""
    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-ar", str(sample_rate),
        "-ac", "1",
        "-f", "wav",
        output_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.returncode == 0
    except Exception as e:
        logger.error(f"ffmpeg conversion failed: {e}")
        return False


# ─── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "model": str(MODEL_PATH.name),
        "vad": str(VAD_PATH.name),
        "binary": str(BIN_PATH.name),
    }


@app.get("/audio/test_silence.wav")
async def get_test_audio():
    path = BASE_DIR / "audio" / "test_silence.wav"
    if path.exists():
        return FileResponse(path, media_type="audio/wav")
    raise HTTPException(status_code=404, detail="Test audio not found")


async def _process_audio(
    file: Optional[UploadFile] = None,
    audio_url: Optional[str] = None,
    keep_tags: bool = False,
    vad_maxseg: Optional[int] = None,
) -> ASRResponse:
    """Core transcription logic shared by all endpoints."""
    with tempfile.TemporaryDirectory(prefix="asr_") as tmpdir:
        raw_path = os.path.join(tmpdir, "input_audio")
        wav_path = os.path.join(tmpdir, "audio_16k.wav")

        # Step 1: Get audio data
        if file:
            content = await file.read()
            ext = Path(file.filename or "audio.wav").suffix.lower()
            raw_path += ext if ext else ".wav"
            with open(raw_path, "wb") as f:
                f.write(content)
            logger.info(f"Received upload: {file.filename} ({len(content)} bytes)")
        elif audio_url:
            try:
                logger.info(f"Downloading audio from: {audio_url}")
                resp = requests.get(audio_url, timeout=30, stream=True)
                resp.raise_for_status()
                ct = resp.headers.get("content-type", "")
                ext = ".wav"
                if "mp3" in ct or "mpeg" in ct:
                    ext = ".mp3"
                elif "ogg" in ct:
                    ext = ".ogg"
                elif "flac" in ct:
                    ext = ".flac"
                elif "webm" in ct:
                    ext = ".webm"
                elif "mp4" in ct or "m4a" in ct:
                    ext = ".m4a"
                raw_path += ext
                with open(raw_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        f.write(chunk)
                logger.info(f"Downloaded audio: {os.path.getsize(raw_path)} bytes")
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Failed to download audio: {e}")

        # Step 2: Convert to 16kHz mono WAV
        is_wav_16k = raw_path.endswith(".wav")
        if is_wav_16k:
            try:
                probe = subprocess.run(
                    ["ffprobe", "-v", "quiet", "-show_entries", "stream=sample_rate,channels,codec_name",
                     "-of", "csv=p=0", raw_path],
                    capture_output=True, text=True, timeout=10,
                )
                parts = probe.stdout.strip().split(",")
                if len(parts) >= 3 and parts[0] == "pcm_s16le" and parts[1] == "1" and parts[2] == "16000":
                    wav_path = raw_path
                else:
                    is_wav_16k = False
            except Exception:
                is_wav_16k = False

        if not is_wav_16k or raw_path != wav_path:
            logger.info("Converting audio to 16kHz mono WAV...")
            if not convert_audio(raw_path, wav_path):
                raise HTTPException(status_code=400, detail="Audio conversion failed. Is ffmpeg installed?")

        # Step 3: Run ASR
        result = run_sensevoice(wav_path, keep_tags=keep_tags, vad_maxseg=vad_maxseg)

        if result.get("error"):
            return ASRResponse(text="", processing_time=result.get("duration", 0), error=result["error"])

        return ASRResponse(
            text=result["text"],
            processing_time=result.get("duration", 0),
            language=result.get("language"),
        )


@app.post("/asr", response_model=ASRResponse)
async def transcribe(
    file: Optional[UploadFile] = File(None),
    audio_url: Optional[str] = Query(None, description="URL to download audio from"),
    keep_tags: bool = Query(False, description="Keep language/emotion/event tags"),
    vad_maxseg: Optional[int] = Query(None, description="Max VAD segment length in ms"),
):
    """Transcribe audio from file upload or URL."""
    if not file and not audio_url:
        raise HTTPException(status_code=400, detail="Provide 'file' upload or 'audio_url' parameter")
    return await _process_audio(file=file, audio_url=audio_url, keep_tags=keep_tags, vad_maxseg=vad_maxseg)


@app.post("/v1/audio/transcriptions")
async def openai_transcriptions(
    file: UploadFile = File(...),
    model: Optional[str] = Form("sensevoice-small"),
    language: Optional[str] = Form(None),
    prompt: Optional[str] = Form(None),
    response_format: Optional[str] = Form("json"),
    temperature: Optional[float] = Form(None),
):
    """OpenAI Whisper-compatible transcription endpoint.

    Drop-in replacement for POST /v1/audio/transcriptions.
    Accepts the same multipart form parameters as the OpenAI Whisper API.
    The 'model', 'language', 'prompt', and 'temperature' parameters are accepted
    for compatibility but ignored (SenseVoice handles these automatically).
    """
    result = await _process_audio(file=file)

    if response_format == "text":
        return PlainTextResponse(result.text)

    # json / verbose_json / srt / vts all return JSON with "text" field
    return {"text": result.text}


# ─── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="SenseVoice ASR Server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address")
    parser.add_argument("--port", type=int, default=9000, help="Port (default: 9000)")
    args = parser.parse_args()

    logger.info(f"Starting SenseVoice ASR Server on {args.host}:{args.port}")
    logger.info(f"  Model: {MODEL_PATH}")
    logger.info(f"  VAD:   {VAD_PATH}")
    logger.info(f"  Binary: {BIN_PATH}")

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
