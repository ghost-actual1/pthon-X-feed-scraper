# X Feed Scraper

**Read X/Twitter without paying for API access.**

No API keys. No developer account. No $100/month "Basic" tier. Just a browser session and Playwright doing what browsers do — reading web pages.

This tool scrapes X/Twitter timelines using a persistent Chromium browser profile, extracts posts with full metadata, and returns clean JSON you can pipe into any AI system, database, webhook, or script.

---

## Why This Exists

In 2023, Twitter became X and killed its free API tier. What used to be a simple REST call now costs $100–5000/month depending on access level. For hobbyists, indie developers, and anyone running local AI systems, that's absurd — especially when all you want to do is *read your own timeline*.

This tool gives that back. It works exactly like you sitting at your computer and scrolling through X, except it's automated and returns structured data instead of pixels.

---

## What It Does

- **Reads your timeline** — posts, replies, Following tab
- **Multi-account support** — primary, alt, and bot accounts via X's account switcher
- **Checks notifications/mentions** — who liked, replied, mentioned you
- **Scrapes any user's timeline** — public profiles while logged in
- **Posts replies** — type and click, like a human would
- **Returns clean JSON** — every post with text, author, handle, engagement metrics, timestamps, media flags, tweet IDs and URLs
- **Webhook support** — POST results to any URL (your AI, a database, whatever)
- **CLI + Library** — use from the terminal or import into your Python project

---

## Quick Start

### Install

```bash
pip install playwright
playwright install chromium
```

Optional (for webhook support without the urllib fallback):
```bash
pip install aiohttp
```

### First-Time Setup

Log into your X account(s) in a real browser window. Your session gets saved for future headless use:

```bash
python x_feed.py setup
```

A Chromium window opens to `x.com/login`. Log in, add any additional accounts via X's account switcher, then close the window. Done.

### Set Your Handle

```bash
# Simple — single account
export XFEED_USERNAME="myhandle"

# Advanced — multiple accounts
export XFEED_HANDLES='[
    {"username": "myhandle", "role": "primary", "alias": "main"},
    {"username": "myalt", "role": "alt", "alias": "backup"},
    {"username": "mybotaccount", "role": "bot", "alias": "bot"}
]'
```

### Check Your Feed

```bash
# Human-readable output
python x_feed.py check

# Raw JSON (pipe to jq, your AI, whatever)
python x_feed.py check --json

# POST results to your AI's webhook
python x_feed.py check --webhook http://localhost:5000/feed

# Check a specific user
python x_feed.py user elonmusk

# Check notifications
python x_feed.py notifications
```

---

## CLI Reference

| Command | Description |
|---------|-------------|
| `setup` | Open browser for manual X login |
| `check` | Check your feed (all configured handles) |
| `check --json` | Output raw JSON |
| `check --webhook URL` | POST results to a URL |
| `check --no-cooldown` | Ignore the 15-min cooldown |
| `check --max N` | Max posts to scrape (default: 30) |
| `user USERNAME` | Scrape a specific user's timeline |
| `user USERNAME --max N` | With post limit |
| `notifications` | Check your notification inbox |
| `reply URL "text"` | Reply to a tweet |
| `reply URL "text" --no-switch` | Reply without switching to bot account |
| `handles` | List configured handles |

---

## Library Usage

Import and use in any Python project:

```python
import asyncio
from x_feed import XFeedScraper

async def main():
    scraper = XFeedScraper(
        handles=[
            {"username": "myhandle", "role": "primary", "alias": "main"},
        ]
    )
    
    # Check feed
    results = await scraper.check_feed()
    
    for post in results["posts"]:
        print(f"@{post['handle']}: {post['text'][:100]}")
        print(f"  ❤️{post['likes']}  🔁{post['reposts']}  💬{post['replies']}")

asyncio.run(main())
```

### With Callback

```python
async def my_handler(posts, metadata):
    """Called after each feed check with all scraped posts."""
    for post in posts:
        # Send to your AI, database, whatever
        await my_ai.process(post["text"], post["handle"])

scraper = XFeedScraper(
    handles=[{"username": "myhandle", "role": "primary", "alias": "main"}],
    on_posts=my_handler,
)
```

### With Webhook

```python
scraper = XFeedScraper(
    handles=[{"username": "myhandle", "role": "primary", "alias": "main"}],
    webhook_url="http://localhost:5000/x-feed",
)
# Results get POSTed as JSON after each check
await scraper.check_feed()
```

---

## Integration Examples

### Ollama

```python
import asyncio, json, httpx
from x_feed import XFeedScraper

async def feed_to_ollama(posts, metadata):
    feed_text = "\n".join(
        f"@{p['handle']}: {p['text']}" for p in posts[:15]
    )
    
    async with httpx.AsyncClient() as client:
        resp = await client.post("http://localhost:11434/api/generate", json={
            "model": "llama3.1",
            "prompt": f"Analyze this X/Twitter feed and summarize the key topics:\n\n{feed_text}",
            "stream": False,
        })
        print(resp.json()["response"])

scraper = XFeedScraper(
    handles=[{"username": "myhandle", "role": "primary", "alias": "main"}],
    on_posts=feed_to_ollama,
)
asyncio.run(scraper.check_feed())
```

### llama.cpp / llama-server

```python
async def feed_to_llamacpp(posts, metadata):
    feed_text = "\n".join(
        f"@{p['handle']}: {p['text']}" for p in posts[:15]
    )
    
    # llama-server /completion endpoint (ChatML or whatever your model uses)
    prompt = f"<|im_start|>user\nAnalyze this feed:\n{feed_text}<|im_end|>\n<|im_start|>assistant\n"
    
    async with httpx.AsyncClient() as client:
        resp = await client.post("http://localhost:8080/completion", json={
            "prompt": prompt,
            "n_predict": 1000,
        })
        print(resp.json()["content"])
```

### Open WebUI / Any OpenAI-Compatible API

```python
async def feed_to_openai_api(posts, metadata):
    feed_text = "\n".join(
        f"@{p['handle']}: {p['text']}" for p in posts[:15]
    )
    
    async with httpx.AsyncClient() as client:
        resp = await client.post("http://localhost:3000/v1/chat/completions", json={
            "model": "my-model",
            "messages": [
                {"role": "user", "content": f"Summarize this X feed:\n\n{feed_text}"}
            ],
        })
        print(resp.json()["choices"][0]["message"]["content"])
```

### Cron Job (Scheduled Scraping)

```bash
# Check feed every 30 minutes, POST to your AI
*/30 * * * * cd /path/to/x_feed_scraper && XFEED_USERNAME=myhandle python x_feed.py check --webhook http://localhost:5000/feed --no-cooldown 2>> /var/log/x_feed.log
```

---

## Post Data Format

Every scraped post returns this structure:

```json
{
    "author": "Display Name",
    "handle": "username",
    "text": "The full post text (up to 1000 chars)",
    "tweet_id": "1234567890",
    "tweet_url": "https://x.com/username/status/1234567890",
    "replies": 5,
    "reposts": 12,
    "likes": 47,
    "views": 1200,
    "timestamp": "2025-01-15T14:30:00.000Z",
    "is_repost": false,
    "has_image": true,
    "has_video": false,
    "source": "following",
    "source_handle": "myhandle",
    "source_alias": "main"
}
```

---

## Multi-Account Roles

| Role | What gets scraped | Use case |
|------|-------------------|----------|
| `primary` | Posts/replies + Following tab | Your main account |
| `alt` | Posts/replies only | Backup if main gets locked |
| `bot` | Notifications/mentions | Your AI's account — checks for @tags |

All accounts use a single browser profile. Log into all of them during `setup` using X's built-in account switcher (the avatar button bottom-left on desktop).

---

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `XFEED_USERNAME` | Single account handle | (none) |
| `XFEED_HANDLES` | JSON array of account configs | (none) |
| `XFEED_PROFILE` | Browser profile directory | `~/.x_feed_scraper/browser_profile` |

### Constructor Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `handles` | list | from env | Account configs |
| `profile_dir` | str | `~/.x_feed_scraper/...` | Browser profile path |
| `max_posts` | int | 30 | Max posts per check |
| `scroll_pause` | float | 2.0 | Seconds between scrolls |
| `max_scrolls` | int | 5 | Max scroll attempts |
| `page_timeout` | int | 30000 | Page load timeout (ms) |
| `cooldown_minutes` | int | 15 | Min time between checks |
| `user_agent` | str | Chrome 131 | Browser user agent |
| `on_posts` | callable | None | Async callback for posts |
| `webhook_url` | str | None | URL to POST results to |

---

## How It Works

1. **Setup:** You log into X in a real Chromium browser. Playwright saves the cookies/session to a local directory.
2. **Scraping:** On each `check_feed()`, Playwright launches headless Chromium reusing those saved cookies — X sees a normal logged-in browser.
3. **Extraction:** JavaScript runs in-page to query the DOM for tweet articles, extracting text, metadata, engagement metrics, and URLs.
4. **Output:** Clean JSON returned to your code, fired to callbacks, or POSTed to webhooks.

The browser has anti-detection defaults enabled (automation flags disabled, realistic user agent). It behaves like a normal Chrome session because it basically *is* one.

---

## Troubleshooting

**"Session expired"** — Run `python x_feed.py setup` again to re-login.

**"Playwright not installed"** — Run `pip install playwright && playwright install chromium`.

**Posts come back empty** — X might be serving a different page layout. Check if you can see tweets when running setup in headed mode. X occasionally changes DOM structure; the `data-testid` selectors are fairly stable but can break.

**"Already checking feed"** — The scraper locks to prevent concurrent browser sessions. Wait for the current check to finish or restart your script.

**Browser profile locked** — If the script crashes mid-check, a `SingletonLock` file may remain. The scraper auto-cleans this, but you can manually delete `SingletonLock` from your profile directory.

**Rate limiting / CAPTCHAs** — X may flag rapid automated access. The default 15-minute cooldown helps avoid this. If you get CAPTCHAs, increase the cooldown or run `setup` to solve them manually.

---

## Legal Note

This tool automates a web browser to read publicly-displayed content the same way any browser extension or accessibility tool would. It does not bypass authentication, break encryption, or access non-public data. It reads your own timeline using your own logged-in session.

That said, X's Terms of Service prohibit automated scraping. Use at your own discretion. This tool is provided as-is with no warranty.

---

## License

MIT — do whatever you want with it.

---

## Credits

Built by [ghost-actual](https://huggingface.co/ghost-actual) as part of the MORK Systems local AI ecosystem. Originally designed to feed X timeline context into a fully self-hosted AI assistant running on consumer hardware.

If Elon wants to charge $100/month to read tweets, we'll just read them ourselves.
