# Video transcripts for the 2026-11-04 export

`videos-2026-11-04.json` lists the 25 videos in the 114 new bookmarks that need transcribing: 23 posted in the bookmarks themselves, 2 inside posts they quote. Each entry has a `key` (`<post id>_<video number>`), the post link, the duration, and the smallest mp4 link where the export has one. The two quoted-post videos have no mp4 link; get their audio from the post link.

`transcribe.py` does the same as the second brain's pipeline: ffmpeg pulls only the audio (16 kHz mono), Whisper large-v3-turbo (mlx) transcribes it on the Mac, and stretches Whisper marks as non-speech or repeated filler are dropped.

```
python3 transcribe/transcribe.py
```

It writes one file per video to `transcribe/transcripts/<key>.json` and skips any already done. Commit that folder to this branch when finished. The repo ignores `*.json`, so add it with `git add -f transcribe/transcripts`.

Each output file looks like this:

```json
{"key": "2116593247854453244_1", "duration_s": 1843, "model": "mlx-community/whisper-large-v3-turbo",
 "transcribed": "2026-11-04", "status": "ok", "language": "en", "words": 5120,
 "segments": [{"start": 0.0, "end": 4.2, "text": "..."}]}
```

`status` is `ok`, `no-speech` (under 25 words) or `no-audio`. Failures are not written, so a re-run retries them.
