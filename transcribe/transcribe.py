#!/usr/bin/env python3
"""
transcribe.py — transcribe the videos listed in videos-2026-11-04.json.

    python3 transcribe/transcribe.py

Same method as the second brain's transcribe_videos.py: ffmpeg pulls only the audio
(16 kHz mono wav, deleted afterwards), Whisper large-v3-turbo transcribes it with mlx,
and non-speech or looping text is dropped. Shortest videos first; finished ones are skipped.
Videos without an mp4 link (from quoted posts) are fetched with yt-dlp if it is installed.
"""
import json, shutil, subprocess, tempfile, time
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "transcripts"
MODEL = "mlx-community/whisper-large-v3-turbo"


def clean(segments):
    kept, seen = [], set()
    for s in segments:
        t = s["text"].strip()
        if not t:
            continue
        if s.get("no_speech_prob", 0) > 0.6 and s.get("avg_logprob", 0) < -1.0:
            continue                                   # Whisper's own "this is not speech" signal
        if s.get("compression_ratio", 0) > 2.4:
            continue                                   # looping, repeated text
        if t.lower() in seen and len(t.split()) <= 6:
            continue                                   # the same short filler line again
        seen.add(t.lower())
        kept.append({"start": round(s["start"], 1), "end": round(s["end"], 1), "text": t})
    return kept


def audio(v, wav):
    if v["mp4"]:
        src = v["mp4"]
    elif shutil.which("yt-dlp"):
        src = subprocess.run(["yt-dlp", "-g", "-f", "bestaudio/best", v["post"]],
                             capture_output=True, text=True).stdout.strip().splitlines()[-1:]
        src = src[0] if src else None
    else:
        return "no mp4 link and yt-dlp is not installed"
    if not src:
        return "could not find the video"
    p = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", src, "-vn",
                        "-ac", "1", "-ar", "16000", str(wav)], capture_output=True, text=True)
    return None if p.returncode == 0 and wav.exists() else (p.stderr.strip()[-200:] or "ffmpeg failed")


def main():
    import mlx_whisper
    videos = json.loads((HERE / "videos-2026-11-04.json").read_text())
    OUT.mkdir(exist_ok=True)
    todo = [v for v in videos if not (OUT / f"{v['key']}.json").exists()]
    print(f"{len(todo)} of {len(videos)} videos to transcribe", flush=True)
    for i, v in enumerate(todo, 1):
        t0 = time.time()
        rec = {"key": v["key"], "duration_s": v["duration_s"], "model": MODEL, "transcribed": time.strftime("%Y-%m-%d")}
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "a.wav"
            err = audio(v, wav)
            if err:
                rec.update(status="no-audio" if "does not contain any stream" in err or "matches no streams" in err
                           else "failed", error=err, segments=[])
            else:
                r = mlx_whisper.transcribe(str(wav), path_or_hf_repo=MODEL, condition_on_previous_text=False, verbose=None)
                segs = clean(r["segments"])
                words = sum(len(s["text"].split()) for s in segs)
                rec.update(language=r.get("language"), words=words, segments=segs,
                           status="ok" if words >= 25 else "no-speech")
        if rec["status"] != "failed":
            (OUT / f"{v['key']}.json").write_text(json.dumps(rec, ensure_ascii=False))
        print(f"  {i}/{len(todo)} {v['key']} -> {rec['status']} {rec.get('words', '')} words in {time.time() - t0:.0f}s "
              f"{rec.get('error', '')[-120:]}", flush=True)


if __name__ == "__main__":
    main()
