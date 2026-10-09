# x-bookmarks-extract

Export every X bookmark on an account with its full content, including the
bodies of X Articles. A plain export leaves those empty.

On a new device, open Claude Code in this folder and say:

> Extract my X bookmarks from the last six months.

`CLAUDE.md` has the full procedure. The manual version is below.

## Setup

```bash
git clone https://github.com/Jyoti-Ranjan-Das845/x-bookmarks-extract.git
cd x-bookmarks-extract
pip install -r requirements.txt

# the xarchive Chrome extension, pinned to the reviewed commit
git clone https://github.com/sytelus/xarchive.git ~/Tools/xarchive
git -C ~/Tools/xarchive checkout ae9d0263617a200ce142e64fb2ddc6301ebf19cf
```

Requires Python 3.9+ and Google Chrome.

## Run

1. **Export.** `chrome://extensions` → Developer mode → Load unpacked →
   `~/Tools/xarchive`. Visit x.com, click the xarchive icon, **Full refresh**,
   then **Download latest snapshot**.

2. **Complete.**
   ```bash
   python3 complete.py ~/Downloads/xarchive_<handle>_<date>.json --launch-chrome --since 6m
   ```
   On the first run a Chrome window opens on X's login page. Sign in, and
   the script carries on by itself.

3. **Collect** `*-complete.json` / `*-complete.jsonl` from Downloads.

## Notes

- `--since` filters by publish date. X doesn't expose when you bookmarked something.
- If xarchive reports a partial scan, use **Refresh X connection**, run **Full refresh**
  again, and download the **combined archive**.
- Afterwards, delete `.chrome-profile-x/` and `.cache/`. They hold an X session and fetched text.
