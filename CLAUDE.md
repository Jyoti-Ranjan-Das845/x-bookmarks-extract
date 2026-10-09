# x-bookmarks-extract

Extract every X (Twitter) bookmark on an account into one JSON file where
**every record has its full content**. The output gets carried back to
another machine for normalization and clustering. That later work does not
happen here. This repo only extracts.

## The goal of a run

The user names a time window, usually "the last six months". Produce
`<export>-complete.json` and `<export>-complete.jsonl` with:

- every bookmark in that window
- no record with empty `content`
- a report saying the scan's coverage

Then tell the user where the files are so they can copy them off this device.

## Pipeline: two stages

### Stage 1: xarchive (the user does this in Chrome)

[xarchive](https://github.com/sytelus/xarchive) is a Chrome extension. It
reads bookmarks through X's internal GraphQL API, 100 per page, at 2.5s or
more between pages. It is not on the Chrome Web Store, so it gets loaded unpacked.

**Use the audited commit.** The extension was code-reviewed at
`ae9d0263617a200ce142e64fb2ddc6301ebf19cf`. Its network calls go only to
`x.com` and `abs.twimg.com`. It has no telemetry, no obfuscation, and its host
permissions are limited to X. Pin to that commit:

```bash
git clone https://github.com/sytelus/xarchive.git
cd xarchive && git checkout ae9d0263617a200ce142e64fb2ddc6301ebf19cf
```

If the user wants a newer version, re-review its network calls first
(`grep -rn "fetch(" --include=*.js . | grep -v vendor/ | grep -v tests/`).
Do not skip this. The extension holds the user's X session headers.

Clone it somewhere **permanent** (for example `~/Tools/xarchive`), not into a temp
folder. An unpacked extension breaks if its folder disappears.

What the user does by hand (Claude cannot open `chrome://` pages):

1. `chrome://extensions` → turn on Developer mode → **Load unpacked** → pick the xarchive folder
2. Open x.com once while signed in, so the extension captures request headers
3. Click the xarchive toolbar icon → **Full refresh**
4. Wait. Keep that tab open. Closing it stops the scan.
5. Click **Download latest snapshot**. The file lands in Downloads as
   `xarchive_<handle>_<date>.json`

**If the scan stops early** (the panel says "Pagination repeated or stopped
making progress", or the JSON has `export_metadata.collection.reason ==
"pagination_guard"`): click **Refresh X connection**, then **Full refresh**
again. Scans merge, so a second scan adds to the first. Then download the
**combined archive**. Check whether the oldest post now reaches back past
the user's window. If it does not, and the scan still says partial, say so plainly.
Do not present partial coverage as complete.

**Do not drive Chrome with browser automation while xarchive runs.** When
an automation or debugging session is attached, Chrome blocks every
`chrome-extension://` page with `ERR_BLOCKED_BY_CLIENT`. Close any
automation tabs first.

### Stage 2: complete.py (Claude runs this)

```bash
pip install -r requirements.txt
python3 complete.py ~/Downloads/xarchive_<handle>_<date>.json --launch-chrome --since 6m
```

This step fills in what xarchive misses:

| Gap | Cause | Fix |
|---|---|---|
| Record has no text at all | Post is only a link to an X Article; the body lives on a separate page | Opens the article page, reads the body |
| Short comment with no topic ("Accurate") | Substance is in the quoted post | Adds the quoted post's text into `content` |

**Detection rule:** a record needs a fetch when an `entities.urls[].expanded_url`
contains `/i/article/`. Do NOT use "has an expanded_url" as the rule. Most
posts with a URL already have real text and link to outside sites. Fetch whenever
an article is linked, even if the post also has text. The script then keeps both.

`--launch-chrome` starts a **separate** Chrome with its own profile
(`.chrome-profile-x/`) and a debug port on 9222. The first run opens an X
login page and waits up to 5 minutes for the user to sign in. **The user types
their own password. Claude never does.** Later runs reuse that profile.
The script closes this Chrome when it finishes.

Why a separate profile: Chrome refuses to open a debug port on the default
profile, because that port would expose every signed-in session. Do not try to
work around this by copying the user's real Chrome profile or cookie
database. That exposes credentials for every site, not just X.

Pacing: 3 to 6.5s random delay between article pages, plus a 20 to 35s rest every 12.
Do not speed this up. X flags regular, fast access patterns.

Fetched bodies are cached in `.cache/articles/<tweet_id>.json`. Re-running
only fetches new articles or ones that failed before.

### Flags

| Flag | Meaning |
|---|---|
| `--since 6m` | Keep posts from the last 6 months. Also accepts `180d`, `1y`, `2w`, `YYYY-MM-DD` |
| `--launch-chrome` | Start the separate Chrome automatically |
| `--chrome-path PATH` | Chrome executable if auto-detection fails (works on macOS, Linux, Windows) |
| `--no-fetch` | Analyse the export and report gaps without opening a browser |
| `-o PREFIX` | Output path prefix (default: next to the input file) |

**`--since` filters on the post's publish date, not on when it was bookmarked.**
X does not expose bookmark timestamps. A post from 2024 that the user bookmarked
last week falls outside `--since 6m`. If the user means "what I bookmarked
in the last six months", run without `--since` and tell them about this limit.

## Output schema

Each record keeps every xarchive field and adds:

| Field | Content |
|---|---|
| `content` | Text to use: the post's own words, plus the article body and/or the quoted post when present |
| `content_type` | `post`, `article`, `post+article`, or `empty` |
| `url` | Canonical post URL |
| `article` | `{url, body, chars}` when an article was fetched |

Kept untouched for later: `media[]` (image URLs, and video MP4 `variants` at
every bitrate), `entities.urls[].expanded_url` (outbound links), `quoted_tweet`,
`author`, `metrics`, `created_at`.

## Verifying a run

Run this check. Do not trust the summary line alone.

```bash
python3 - EXPORT.json EXPORT-complete.json <<'EOF'
import json, sys
src = json.load(open(sys.argv[1]))["bookmarks"]
out = json.load(open(sys.argv[2]))
S, O = {r["tweet_id"] for r in src}, {r["tweet_id"] for r in out}
print("missing:", len(S - O), "(non-zero is expected only with --since)")
print("duplicates:", len(out) - len(O))
print("empty content:", sum(1 for r in out if not r["content"].strip()))
art = [r for r in out if any("/i/article/" in u.get("expanded_url", "")
       for u in (r.get("entities") or {}).get("urls", []))]
print("articles fetched:", sum(1 for r in art if r.get("article")), "/", len(art))
EOF
```

Very short records (under 40 chars) are usually real. "Accurate" is a whole
post. Check whether they carry `media` before calling them failures.

## Out of scope here

- **Video transcription.** Video posts keep their MP4 URLs in `media[].variants`.
  Transcribing needs a download, then ffmpeg to pull the audio, then Whisper.
  That gets decided later, on the other machine.
- **Text inside images (OCR).** The user declined it.
- **Thread continuations.** The user wants the bookmarked post only.
- **Outbound links to outside sites.** Kept as URLs, handled at synthesis time.
- Normalization and clustering.

## Cleanup when done

Leftover processes stopped the user's Mac from shutting down in an earlier
session. After a run, check:

```bash
ps aux | grep -E "chrome-profile-x|remote-debugging-port|complete\.py" | grep -v grep
```

Kill anything listed. If the user is finished with this device, delete
`.chrome-profile-x/` and `.cache/`. They hold an X session and article text.
