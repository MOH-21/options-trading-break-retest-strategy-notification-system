"""Download YouTube audio and transcribe locally with faster-whisper.

Usage:
    python youtube_transcriber.py <youtube_url>
    python youtube_transcriber.py --channel-latest <channel_tab_url>
    python youtube_transcriber.py --source jdub
    python youtube_transcriber.py --source scarface
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

from faster_whisper import WhisperModel

TRANSCRIPT_DIR = Path(__file__).parent / "transcripts"
MODEL_SIZE = "small.en"

SOURCES = {
    "jdub": "https://www.youtube.com/channel/UCp90hB8_sqLcEEXVgiUCbcw/streams",
    "scarface": "https://www.youtube.com/@ScarfaceUnscripted/videos",
}


def resolve_latest_from_channel(channel_url: str) -> str:
    result = subprocess.run(
        ["yt-dlp", "--flat-playlist", "--print", "id", "-I", "1", channel_url],
        capture_output=True, text=True, check=True,
    )
    video_id = result.stdout.strip().splitlines()[-1]
    return f"https://www.youtube.com/watch?v={video_id}"


def download_audio(url: str) -> Path:
    TRANSCRIPT_DIR.mkdir(exist_ok=True)
    id_result = subprocess.run(
        ["yt-dlp", "--print", "id", "--no-playlist", url],
        capture_output=True, text=True, check=True,
    )
    video_id = id_result.stdout.strip().splitlines()[-1]
    audio_path = TRANSCRIPT_DIR / f"{video_id}.m4a"

    if audio_path.exists():
        print(f"[cache] audio already downloaded: {audio_path.name}")
        return audio_path

    subprocess.run([
        "yt-dlp", "-f", "140",
        "-o", str(TRANSCRIPT_DIR / "%(id)s.%(ext)s"),
        "--no-playlist", url,
    ], check=True)
    return audio_path


def transcribe(audio_path: Path) -> Path:
    out_path = audio_path.with_suffix(".txt")
    print(f"[whisper] loading model {MODEL_SIZE} (CPU, int8)")
    model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")
    print(f"[whisper] transcribing {audio_path.name}")
    segments, info = model.transcribe(str(audio_path), beam_size=5, language="en")
    with out_path.open("w") as f:
        for seg in segments:
            f.write(f"[{seg.start:7.1f}s -> {seg.end:7.1f}s] {seg.text.strip()}\n")
    print(f"[whisper] detected duration: {info.duration:.0f}s")
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("url", nargs="?", help="YouTube video URL")
    group.add_argument("--channel-latest", metavar="CHANNEL_URL",
                       help="Resolve to latest video in a channel tab (e.g. .../streams or .../videos)")
    group.add_argument("--source", choices=SOURCES.keys(),
                       help=f"Use a preset source: {', '.join(SOURCES)}")
    args = parser.parse_args()

    if args.source:
        url = resolve_latest_from_channel(SOURCES[args.source])
    elif args.channel_latest:
        url = resolve_latest_from_channel(args.channel_latest)
    else:
        url = args.url

    print(f"[input] {url}")
    t0 = time.monotonic()
    audio_path = download_audio(url)
    t1 = time.monotonic()
    print(f"[download] {audio_path.name} ({audio_path.stat().st_size / 1e6:.1f} MB) in {t1 - t0:.1f}s")

    out_path = transcribe(audio_path)
    t2 = time.monotonic()
    print(f"[done] transcript: {out_path} ({t2 - t1:.1f}s transcribe, {t2 - t0:.1f}s total)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
