# Transcribe the long X videos

The task on this branch: turn the speech in 109 long videos (30 minutes to 4 hours,
134 hours of audio in total) into text, as fast as this Mac allows, without
lowering quality. The videos come from posts the user bookmarked on X. The
transcripts go back to another machine, where they become part of a personal
knowledge base.

Everything needed is in this folder:

| File | What it is |
|---|---|
| `videos_long.json` | The 109 videos: `key` (post id + video number), `url` (direct mp4 on X's video server), `ms` (length) |
| `transcribe.py` | Pulls the audio, transcribes it with Whisper large-v3-turbo, saves `transcripts/<key>.json` |
| `transcripts/` | The output. One JSON file per finished video |

## Fixed choices. Do not change them

- **Model: `mlx-community/whisper-large-v3-turbo`.** It was compared with
  whisper-small and whisper-base on these videos. The smaller models were
  faster but swapped in wrong words ("logistics" became "religiousics",
  "master's" became "NASA's"). Speed must come from running in parallel, not
  from a smaller model.
- **Output format and the `clean()` rules.** The other machine reads these
  files as they are. Same keys, same segment shape, same cleanup.
- **Audio only.** No video file is ever kept. ffmpeg reads the sound and a
  temp wav is deleted after each video.

## Steps

### 1. Check the machine and set up

```bash
sysctl -n machdep.cpu.brand_string hw.memsize      # Apple Silicon is required (mlx)
brew install ffmpeg                                # if `ffmpeg -version` fails
python3 -m venv .venv && source .venv/bin/activate
pip install mlx-whisper
```

The model (about 1.6 GB) downloads on first use.

### 2. Measure before running: the benchmark

```bash
python3 transcribe.py --benchmark
```

This takes the first 6 minutes of one video, then transcribes it with 1, 2, 3
and 4 workers at the same time. Each worker is a separate process with its own
copy of the model, on the same GPU. It prints the total speed for each count
and recommends a `--workers` value, with an estimate for all 109 videos.

How many workers it tries depends on memory: about 3 GB per worker, plus 4 GB
for the system. A 24 GB Mac Mini tries up to 4.

**Show the user the benchmark table and the recommendation before starting
the full run.** More workers only help while the GPU has spare capacity. If 3
workers are barely faster than 2 (under 10%), use 2. Fewer processes means less
memory pressure and fewer surprises.

### 3. Run it

```bash
caffeinate -i nohup python3 transcribe.py --workers N > transcribe.log 2>&1 &
```

- `caffeinate -i` keeps the Mac awake. The Mac must also stay on power.
- It goes **longest video first**. A Colab notebook may be working through the
  same list **shortest first**, so the two machines only meet near the end.
  If the user says Colab finished, stop this run (see step 5).
- Each finished video prints one line:
  `12/109 <key> 64 min -> ok 9120 words in 5.2 min · overall 38.0x realtime · about 2.9 h left`
- Check progress with `tail -5 transcribe.log` or
  `ls transcripts | wc -l` (out of 109).

### 4. If something goes wrong

- **A line says `failed`:** the audio could not be pulled, usually because the
  network dropped. It was not saved, so a later re-run retries it. The script
  already waits and retries 3 times per video.
- **Many failures in a row:** the internet is probably down. Stop the run,
  wait, then start it again. Finished videos are skipped.
- **The Mac slows down a lot or swaps** (`memory_pressure` reports low free
  memory): stop, and restart with one worker fewer.
- **Stopping:** `pkill -f transcribe.py`. Nothing is lost except the videos in
  progress.

### 5. Send the transcripts back

When the run ends (`109/109 transcribed ... all done`), or when the user asks:

```bash
git add transcripts
git commit -m "Add transcripts of long X videos"
git push origin long-video-transcription
```

The files are small text JSON, a few MB in total. Then tell the user how many
videos are done, how many had speech, and how long the run took.

## Do not

- Do not switch to a smaller or different model to go faster.
- Do not raise the worker count above what the benchmark recommends.
- Do not download or keep the video files themselves.
- Do not edit `videos_long.json`. It is the agreed list.
