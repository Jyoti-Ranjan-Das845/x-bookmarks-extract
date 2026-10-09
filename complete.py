#!/usr/bin/env python3
"""
complete.py — turn an xarchive export into a complete bookmark corpus.

    python3 complete.py export.json --launch-chrome --since 6m

Reads the JSON xarchive produces, finds records whose content is
missing, fetches it, verifies the result, and writes finished files.
Re-runnable: fetched article bodies are cached, so a second run only
fetches what is new or previously failed.

WHAT IS MISSING AND WHY
  xarchive reads X's bookmark API, which returns post objects. When a
  post is only a link to an X Article, the post object holds the link
  and nothing else — the body lives on its own page. Those records
  arrive empty. This script opens each article page and reads the body.
  Quote-posts also get the quoted post's text folded in, since a short
  comment ("Accurate") carries no topic on its own.

BROWSER
  --launch-chrome starts a separate Chrome (own profile directory, so
  your normal Chrome is untouched) with a local debug port. The first
  time, sign in to X in the window it opens; the script waits for you.
  The profile is reused on later runs. The Chrome it launched is closed
  when the script ends.

OUTPUT (next to the input file unless -o is given)
  <name>-complete.json    every record, with `content` and `content_type`
  <name>-complete.jsonl   same, one record per line
  <name>-report.txt       counts, coverage, anything still empty
"""
from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import json
import os
import platform
import random
import re
import subprocess
import sys
import time
from pathlib import Path

HERE      = Path(__file__).parent
CACHE_DIR = HERE / ".cache" / "articles"
PROFILE   = HERE / ".chrome-profile-x"
PORT      = 9222
LOGIN_WAIT_SECONDS = 300
TAIL      = ["Want to publish your own Article?", "Upgrade to Premium"]

CHROME_CANDIDATES = {
    "Darwin":  ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"],
    "Linux":   ["/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
                "/usr/bin/chromium", "/usr/bin/chromium-browser"],
    "Windows": [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"],
}


# ----------------------------------------------------------------- helpers
def strip_tco(s: str | None) -> str:
    return re.sub(r"https://t\.co/\w+", "", s or "").strip()


def expanded_urls(rec: dict) -> list[str]:
    return [u.get("expanded_url", "")
            for u in (rec.get("entities") or {}).get("urls", [])]


def article_url(rec: dict) -> str | None:
    for u in expanded_urls(rec):
        if "/i/article/" in u:
            return u.replace("http://", "https://")
    return None


def clean_body(text: str) -> str:
    for marker in TAIL:
        i = text.find(marker)
        if i > 200:
            text = text[:i]
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def classify(rec: dict) -> str:
    """'ok', 'article' (needs a fetch), or 'unknown-gap'."""
    if article_url(rec):
        return "article"
    if strip_tco(rec.get("full_text")):
        return "ok"
    if rec.get("media") or rec.get("quoted_tweet") or rec.get("card"):
        return "ok"
    return "unknown-gap"


def created(rec: dict) -> dt.datetime | None:
    try:
        return email.utils.parsedate_to_datetime(rec["created_at"])
    except Exception:
        return None


def parse_since(value: str) -> dt.datetime:
    """'6m', '180d', '2w', '1y' or 'YYYY-MM-DD' -> aware UTC datetime."""
    now = dt.datetime.now(dt.timezone.utc)
    m = re.fullmatch(r"(\d+)\s*([dwmy])", value.strip().lower())
    if m:
        n, unit = int(m.group(1)), m.group(2)
        days = {"d": 1, "w": 7, "m": 30, "y": 365}[unit] * n
        return now - dt.timedelta(days=days)
    return dt.datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)


# ------------------------------------------------------------------ chrome
def find_chrome(explicit: str | None) -> str | None:
    if explicit:
        return explicit if Path(explicit).exists() else None
    for c in CHROME_CANDIDATES.get(platform.system(), []):
        if Path(c).exists():
            return c
    return None


def launch_chrome(chrome: str) -> subprocess.Popen:
    PROFILE.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(
        [chrome, f"--remote-debugging-port={PORT}",
         f"--user-data-dir={PROFILE}", "--no-first-run",
         "--no-default-browser-check", "https://x.com/home"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def stop_chrome(proc: subprocess.Popen | None) -> None:
    """Close the Chrome we started, including its helper processes."""
    if not proc:
        return
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    # Helpers can outlive the parent; sweep anything on our profile dir.
    if platform.system() != "Windows":
        subprocess.run(["pkill", "-f", str(PROFILE)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def connect(p, launch: bool, chrome_path: str | None):
    """Attach to Chrome on the debug port; start one if asked."""
    try:
        return p.chromium.connect_over_cdp(f"http://localhost:{PORT}"), None
    except Exception:
        pass
    if not launch:
        return None, None
    chrome = find_chrome(chrome_path)
    if not chrome:
        print("  Google Chrome not found. Pass --chrome-path /path/to/chrome")
        return None, None
    print(f"  starting Chrome (separate profile: {PROFILE.name}/)")
    proc = launch_chrome(chrome)
    for _ in range(15):
        time.sleep(1.5)
        try:
            return p.chromium.connect_over_cdp(f"http://localhost:{PORT}"), proc
        except Exception:
            continue
    stop_chrome(proc)
    return None, None


def logged_in(page) -> bool:
    for sel in ['[data-testid="SideNav_NewTweet_Button"]',
                '[data-testid="AppTabBar_Home_Link"]']:
        try:
            if page.query_selector(sel):
                return True
        except Exception:
            pass
    return False


def wait_for_login(page) -> bool:
    page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=30_000)
    time.sleep(3)
    if logged_in(page):
        return True
    print("\n  Not signed in to X in the Chrome window that just opened.")
    print(f"  Sign in there — waiting up to {LOGIN_WAIT_SECONDS // 60} minutes...")
    deadline = time.time() + LOGIN_WAIT_SECONDS
    while time.time() < deadline:
        time.sleep(3)
        if logged_in(page):
            print("  signed in.\n")
            time.sleep(2)
            return True
    return False


# ------------------------------------------------------------------- fetch
def fetch_articles(jobs: list[dict], args) -> dict[str, dict]:
    """Fetch article bodies; cache each to disk; return {tweet_id: rec}."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out: dict[str, dict] = {}
    todo = []
    for j in jobs:
        c = CACHE_DIR / f"{j['tweet_id']}.json"
        if c.exists():
            try:
                r = json.loads(c.read_text())
                if r.get("status") == "ok":
                    out[j["tweet_id"]] = r
                    continue
            except Exception:
                pass
        todo.append(j)

    print(f"  cached: {len(out)}   to fetch: {len(todo)}")
    if not todo:
        return out

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  playwright is not installed:  pip install -r requirements.txt")
        return out

    proc = None
    try:
        with sync_playwright() as p:
            browser, proc = connect(p, args.launch_chrome, args.chrome_path)
            if not browser:
                print(f"\n  Could not reach Chrome on port {PORT}.")
                print("  Re-run with --launch-chrome.\n")
                return out

            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            page = ctx.new_page()
            if not wait_for_login(page):
                print("  Timed out waiting for X sign-in. Nothing fetched.")
                return out

            ok = fail = 0
            for i, j in enumerate(todo, 1):
                tid, url, handle = j["tweet_id"], j["url"], j["handle"]
                rec = {"tweet_id": tid, "handle": handle, "article_url": url,
                       "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                   time.gmtime())}
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                    try:
                        page.wait_for_selector("article", timeout=20_000)
                    except Exception:
                        pass
                    time.sleep(2.0)                  # client-side render
                    node = page.query_selector("article")
                    body = clean_body(node.inner_text() if node
                                      else page.inner_text("body"))
                    if len(body) < 250:
                        rec.update(status="too_short", chars=len(body), body=body)
                        fail += 1
                        print(f"  [{i}/{len(todo)}] @{handle:<18} SHORT {len(body)}c")
                    else:
                        rec.update(status="ok", chars=len(body), body=body)
                        out[tid] = rec
                        ok += 1
                        print(f"  [{i}/{len(todo)}] @{handle:<18} ok {len(body):>6}c")
                except Exception as e:
                    rec.update(status="error", error=str(e)[:200])
                    fail += 1
                    print(f"  [{i}/{len(todo)}] @{handle:<18} ERROR {str(e)[:60]}")

                (CACHE_DIR / f"{tid}.json").write_text(
                    json.dumps(rec, ensure_ascii=False))

                if i < len(todo):
                    d = random.uniform(args.min_delay, args.max_delay)
                    if i % 12 == 0:                  # occasional longer rest
                        d = random.uniform(20, 35)
                        print(f"      resting {d:.0f}s")
                    time.sleep(d)

            page.close()
            print(f"\n  fetched: {ok} ok, {fail} failed")
    finally:
        stop_chrome(proc)
    return out


# -------------------------------------------------------------- assemble
def assemble(r: dict, art: dict | None) -> tuple[dict, str]:
    rec = dict(r)
    text = strip_tco(r.get("full_text"))

    q = r.get("quoted_tweet") or {}
    qtext = strip_tco(q.get("full_text"))
    quoted_block = ""
    if qtext:
        qauth = (q.get("author") or {}).get("screen_name", "")
        quoted_block = f"\n\n> quoting @{qauth}:\n> " + qtext.replace("\n", "\n> ")

    if art and text:
        kind, content = "post+article", f"{text}\n\n---\n\n{art['body']}"
    elif art:
        kind, content = "article", art["body"]
    elif text or qtext:
        kind, content = "post", text
    else:
        kind, content = "empty", ""
    if quoted_block:
        content = (content + quoted_block).strip()

    rec["content"] = content
    rec["content_type"] = kind
    rec["url"] = f"https://x.com/{r['author']['screen_name']}/status/{r['tweet_id']}"
    if art:
        rec["article"] = {"url": art["article_url"], "body": art["body"],
                          "chars": art["chars"]}
    return rec, kind


# -------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description="Complete an xarchive bookmark export.")
    ap.add_argument("input", help="xarchive JSON export")
    ap.add_argument("-o", "--out", help="output prefix (default: next to input)")
    ap.add_argument("--since", help="keep bookmarks posted since: 6m, 180d, 1y, or YYYY-MM-DD")
    ap.add_argument("--launch-chrome", action="store_true",
                    help="start a separate Chrome with the debug port")
    ap.add_argument("--chrome-path", help="Chrome executable, if not auto-detected")
    ap.add_argument("--no-fetch", action="store_true", help="analyse only")
    ap.add_argument("--min-delay", type=float, default=3.0)
    ap.add_argument("--max-delay", type=float, default=6.5)
    args = ap.parse_args()

    src = Path(args.input).expanduser()
    if not src.exists():
        print(f"not found: {src}")
        return 1

    data = json.loads(src.read_text())
    records = data["bookmarks"] if isinstance(data, dict) else data
    meta = data.get("export_metadata", {}) if isinstance(data, dict) else {}
    collection = meta.get("collection", {})
    partial = collection.get("bookmarks") == "partial"

    print(f"\nreading {src.name}")
    print(f"  account: @{meta.get('screen_name', '?')}")
    print(f"  records: {len(records)}")

    dates = [d for d in (created(r) for r in records) if d]
    if dates:
        print(f"  date range: {min(dates).date()} -> {max(dates).date()}")

    if args.since:
        cutoff = parse_since(args.since)
        before = len(records)
        records = [r for r in records if (created(r) or cutoff) >= cutoff]
        print(f"  --since {args.since}: keeping posts from {cutoff.date()} "
              f"({len(records)}/{before})")
        if partial and dates and min(dates) > cutoff:
            print(f"  WARNING: export is partial and its oldest post "
                  f"({min(dates).date()}) is newer than the cutoff — older "
                  f"bookmarks may be missing. Re-scan in xarchive.")

    if partial:
        print(f"  NOTE: xarchive marked this scan partial "
              f"({collection.get('reason', '?')})")

    buckets: dict[str, list] = {"ok": [], "article": [], "unknown-gap": []}
    for r in records:
        buckets[classify(r)].append(r)
    print("\ngaps found")
    print(f"  complete already:  {len(buckets['ok'])}")
    print(f"  need article body: {len(buckets['article'])}")
    print(f"  unexplained gaps:  {len(buckets['unknown-gap'])}")

    fetched: dict[str, dict] = {}
    if buckets["article"] and not args.no_fetch:
        print(f"\nfetching {len(buckets['article'])} article bodies")
        jobs = [{"tweet_id": r["tweet_id"], "handle": r["author"]["screen_name"],
                 "url": article_url(r)} for r in buckets["article"]]
        fetched = fetch_articles(jobs, args)

    out, kinds = [], {"post": 0, "article": 0, "post+article": 0, "empty": 0}
    for r in records:
        rec, kind = assemble(r, fetched.get(r["tweet_id"]))
        kinds[kind] += 1
        out.append(rec)

    prefix = Path(args.out) if args.out else src.with_suffix("")
    pj, pl, pr = (Path(f"{prefix}-complete.json"), Path(f"{prefix}-complete.jsonl"),
                  Path(f"{prefix}-report.txt"))
    pj.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    with pl.open("w") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    total = len(out)
    have = total - kinds["empty"]
    lines = [
        f"source        : {src.name}",
        f"account       : @{meta.get('screen_name', '?')}",
        f"generated     : {time.strftime('%Y-%m-%d %H:%M')}",
        f"since filter  : {args.since or 'none'}",
        f"xarchive scan : {'PARTIAL (' + collection.get('reason', '?') + ')' if partial else 'complete'}",
        f"records       : {total}",
        "",
        f"post only     : {kinds['post']}",
        f"article only  : {kinds['article']}",
        f"post+article  : {kinds['post+article']}",
        f"still empty   : {kinds['empty']}",
        "",
        f"with content  : {have}/{total} ({100 * have // total if total else 0}%)",
        f"total chars   : {sum(len(r['content']) for r in out):,}",
    ]
    if kinds["empty"]:
        lines += ["", "still empty:"]
        lines += [f"  @{r['author']['screen_name']:<18} {r['url']}"
                  for r in out if r["content_type"] == "empty"]
    pr.write_text("\n".join(lines) + "\n")

    print("\n" + "\n".join(lines[5:]))
    print(f"\nwrote  {pj}\n       {pl}\n       {pr}")
    if kinds["empty"]:
        print("\nre-run to retry the empty ones (successes are cached)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
