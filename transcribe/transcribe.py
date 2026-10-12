#!/usr/bin/env python3
"""
transcribe.py — transcribe the long X videos with Whisper large-v3-turbo on Apple Silicon.

    python3 transcribe.py --benchmark            # how fast is 1, 2, 3... workers in parallel?
    python3 transcribe.py --workers 2            # transcribe everything, longest video first
    python3 transcribe.py --workers 2 --limit 3  # just a few, to try it

For each video in videos_long.json: ffmpeg pulls only the audio from X's video server
(16 kHz mono wav, deleted afterwards), Whisper large-v3-turbo transcribes it on the GPU
(mlx), and the result goes to transcripts/<key>.json. Each worker is a separate process
with its own copy of the model. Finished videos are skipped, so stopping and re-running
is safe.
"""
import argparse, json, multiprocessing as mp, os, subprocess, tempfile, time
from pathlib import Path

HERE = Path(__file__).parent
VIDEOS = HERE / "videos_long.json"
OUT = HERE / "transcripts"
MODEL = "mlx-community/whisper-large-v3-turbo"
BENCH_SECONDS = 360                      # benchmark on the first 6 minutes of one video


# ------------------------------------------------------------------ shared pieces
def pull_audio(url, wav, seconds=None):
    """Only the sound, 16 kHz mono. Returns None, or the error text."""
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", url, "-vn", "-ac", "1", "-ar", "16000"]
    cmd += ["-t", str(seconds)] if seconds else []
    err = ""
    for attempt in range(3):
        p = subprocess.run(cmd + [str(wav)], capture_output=True, text=True)
        if p.returncode == 0 and wav.exists():
            return None
        err = p.stderr.strip()[-200:]
        time.sleep(15 * (attempt + 1))
    return err or "ffmpeg failed"


def clean(segments):
    """Drop what Whisper marks as non-speech, looping text, and repeated filler lines."""
    kept, seen = [], set()
    for s in segments:
        t = s["text"].strip()
        if not t:
            continue
        if s.get("no_speech_prob", 0) > 0.6 and s.get("avg_logprob", 0) < -1.0:
            continue
        if s.get("compression_ratio", 0) > 2.4:
            continue
        if t.lower() in seen and len(t.split()) <= 6:
            continue
        seen.add(t.lower())
        kept.append({"start": round(s["start"], 1), "end": round(s["end"], 1), "text": t})
    return kept


def whisper(wav):
    import mlx_whisper
    return mlx_whisper.transcribe(str(wav), path_or_hf_repo=MODEL, condition_on_previous_text=False, verbose=None)


def warm_up():
    """Load the model once per worker (transcribing 1 s of silence)."""
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "silence.wav"
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i",
                        "anullsrc=r=16000:cl=mono", "-t", "1", str(wav)], check=True)
        whisper(wav)


def memory_gb():
    return int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout) / 2**30


# ------------------------------------------------------------------ the real run
def work(v):
    out = OUT / f"{v['key']}.json"
    if out.exists():
        return v, "skipped", 0, 0.0, ""
    t0 = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "a.wav"
        err = pull_audio(v["url"], wav)
        if err:
            return v, "failed", 0, time.time() - t0, err   # not saved, so a re-run retries it
        r = whisper(wav)
    segs = clean(r["segments"])
    words = sum(len(s["text"].split()) for s in segs)
    rec = {"key": v["key"], "video_url": v["url"], "duration_s": round(v["ms"] / 1000),
           "model": "mlx-whisper large-v3-turbo", "transcribed": time.strftime("%Y-%m-%d"),
           "language": r.get("language"), "words": words, "segments": segs,
           "status": "ok" if words >= 25 else "no-speech"}
    out.write_text(json.dumps(rec, ensure_ascii=False))
    return v, rec["status"], words, time.time() - t0, ""


def run(workers, order, limit):
    OUT.mkdir(exist_ok=True)
    videos = json.loads(VIDEOS.read_text())
    todo = [v for v in videos if not (OUT / f"{v['key']}.json").exists()]
    todo.sort(key=lambda v: v["ms"], reverse=(order == "longest"))
    todo = todo[:limit] if limit else todo
    total_audio = sum(v["ms"] for v in todo) / 1000
    print(f"{len(todo)} of {len(videos)} videos to do · {total_audio / 3600:.1f} h of audio · "
          f"{workers} worker(s) · {order} first", flush=True)
    from huggingface_hub import snapshot_download
    snapshot_download(MODEL)                           # download once, before the workers start

    start, audio_done = time.time(), 0.0
    with mp.get_context("spawn").Pool(workers, initializer=warm_up) as pool:
        for i, (v, status, words, secs, err) in enumerate(pool.imap_unordered(work, todo), 1):
            if status != "failed":
                audio_done += v["ms"] / 1000
            elapsed = time.time() - start
            speed = audio_done / elapsed if elapsed else 0
            eta = (total_audio - audio_done) / speed / 3600 if speed else 0
            print(f"  {i}/{len(todo)} {v['key']} {v['ms'] / 60000:.0f} min -> {status} {words} words "
                  f"in {secs / 60:.1f} min · overall {speed:.1f}x realtime · about {eta:.1f} h left {err}",
                  flush=True)
    done = len(list(OUT.glob("*.json")))
    print(f"\n{done}/{len(videos)} transcribed in {(time.time() - start) / 3600:.1f} h"
          + (" · all done" if done >= len(videos) else " · run again to retry the rest"), flush=True)


# ------------------------------------------------------------------ benchmark
def bench_worker(wav, barrier, results):
    warm_up()
    barrier.wait()                                     # all workers start at the same moment
    t0 = time.time()
    whisper(wav)
    results.put(time.time() - t0)


def benchmark(max_workers):
    gb = memory_gb()
    limit = max(1, min(max_workers, int((gb - 4) // 3)))   # about 3 GB per worker, 4 GB for the system
    print(f"memory {gb:.0f} GB -> testing up to {limit} parallel worker(s)", flush=True)
    from huggingface_hub import snapshot_download
    snapshot_download(MODEL)
    videos = json.loads(VIDEOS.read_text())
    v = sorted(videos, key=lambda v: v["ms"])[len(videos) // 2]
    tmp = Path(tempfile.mkdtemp())
    wav = tmp / "bench.wav"
    err = pull_audio(v["url"], wav, seconds=BENCH_SECONDS)
    if err:
        raise SystemExit(f"could not pull audio for the benchmark: {err}")
    ctx = mp.get_context("spawn")
    rows = []
    for n in range(1, limit + 1):
        barrier, results = ctx.Barrier(n), ctx.Queue()
        procs = [ctx.Process(target=bench_worker, args=(wav, barrier, results)) for _ in range(n)]
        for p in procs:
            p.start()
        times = [results.get() for _ in range(n)]
        for p in procs:
            p.join()
        wall = max(times)
        speed = n * BENCH_SECONDS / wall
        rows.append((n, wall, speed))
        print(f"  {n} worker(s): {n} x {BENCH_SECONDS // 60} min of audio in {wall:.0f} s -> "
              f"{speed:.1f}x realtime in total", flush=True)
    best = rows[0]
    for r in rows[1:]:
        if r[2] > best[2] * 1.10:                      # only worth it if at least 10% faster
            best = r
    audio_h = sum(x["ms"] for x in videos) / 3.6e6
    print(f"\nrecommended: --workers {best[0]} · all {len(videos)} videos ({audio_h:.0f} h of audio) "
          f"would take about {audio_h / best[2]:.1f} h", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--benchmark", action="store_true", help="measure speed with 1..N parallel workers")
    ap.add_argument("--max-workers", type=int, default=4, help="most workers the benchmark tries")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--order", choices=["longest", "shortest"], default="longest")
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    benchmark(a.max_workers) if a.benchmark else run(a.workers, a.order, a.limit)
