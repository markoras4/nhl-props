"""Give every YouTube item in a day's manifest its own copy with original music (video/music.py) in place of the
licensed song. The channel got copyright strikes over Snap Crackle (freetouse), so YouTube never gets it; TikTok keeps
the licensed song. The video stream is copied unchanged; only the audio is replaced.

    py video/yt_audio.py --manifest video/out/2026-11-28/manifest.json

Each YouTube item's "file" then points at <name>-yt.mp4. Running it again is a no-op for items already done.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import music  # noqa: E402


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(out.strip())


def swap(src: Path, dst: Path, series: str, seed: str) -> None:
    total = duration(src)
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "music.wav"
        music.write_wav(str(wav), music.series_track(series, total, seed=seed))
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-i", str(wav), "-map", "0:v", "-map", "1:a",
                        "-c:v", "copy", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "192k",
                        "-shortest", "-movflags", "+faststart", str(dst)], check=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--manifest", required=True)
    a = ap.parse_args(argv)
    mp = Path(a.manifest)
    man = json.loads(mp.read_text(encoding="utf-8"))
    n = 0
    for it in man.get("items", []):
        if it.get("platform") != "youtube" or not it.get("file") or it["file"].endswith("-yt.mp4"):
            continue
        src = ROOT / it["file"]
        if not src.is_file():
            print(f"  missing: {it['file']}")
            continue
        dst = src.with_name(src.stem + "-yt.mp4")
        try:
            swap(src, dst, it.get("series", "board"), f"{it.get('id', src.stem)}|yt")
        except Exception as e:  # noqa: BLE001
            # never fall back to the licensed audio: drop the item so YouTube skips it
            print(f"::warning::YouTube audio swap failed for {it.get('id')}: {e}")
            it["platform"] = "youtube-skipped"
            continue
        it["file"] = str(dst.resolve().relative_to(ROOT)).replace("\\", "/")
        n += 1
        print(f"  youtube copy with original music: {it['file']}")
    mp.write_text(json.dumps(man, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"yt_audio: {n} YouTube item(s) now use original music")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
