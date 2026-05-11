#!/usr/bin/env python3
"""
X Feed Scraper — Free X/Twitter Timeline Reader
═══════════════════════════════════════════════════
No API keys. No $100/month "Basic" tier. No begging Elon for access.
Just a real browser session + Playwright doing what browsers do: reading web pages.

This tool reads X/Twitter timelines, extracts posts with full metadata,
and returns clean JSON you can pipe into any AI system, database, or script.

Works with any local AI setup — Ollama, llama.cpp, Open WebUI, SillyTavern,
KoboldCpp, text-generation-webui, LM Studio, or your own custom stack.
Also works headless on servers for scheduled scraping.

Features:
    - Persistent browser session (log in once, reuse forever)
    - Multi-account support via X's account switcher
    - Timeline scraping (posts, replies, Following tab)
    - Notification/mention checking
    - Specific user timeline scraping
    - Reply posting (type + click, like a human)
    - Webhook/callback support (POST results anywhere)
    - CLI mode for quick use
    - Library mode for integration into larger systems

Architecture:
    - Single persistent Chromium profile with saved cookies
    - No API keys, no OAuth tokens, no developer account needed
    - Anti-detection defaults (disabled automation flags)
    - Headless by default, headed mode for setup/debugging

Quick Start:
    # Install
    pip install playwright
    playwright install chromium

    # First-time login (opens a real browser window)
    python x_feed.py setup

    # Check your feed
    python x_feed.py check

    # Check a specific user
    python x_feed.py user elonmusk

    # Check with webhook (POST results to your AI)
    python x_feed.py check --webhook http://localhost:5000/feed

    # As a library
    from x_feed import XFeedScraper
    scraper = XFeedScraper()
    results = await scraper.check_feed()

License: MIT — do whatever you want with it.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Callable, Awaitable

log = logging.getLogger("x_feed")

# ══════════════════════════════════════════════════════════════════
# Configuration
# ══════════════════════════════════════════════════════════════════

# Where to store the browser profile (cookies, session data)
DEFAULT_PROFILE_DIR = os.path.expanduser("~/.x_feed_scraper/browser_profile")

# Feed scraping defaults
DEFAULT_MAX_POSTS = 30
DEFAULT_SCROLL_PAUSE = 2.0
DEFAULT_MAX_SCROLLS = 5
DEFAULT_PAGE_TIMEOUT = 30000  # ms
DEFAULT_COOLDOWN_MINUTES = 15

# Browser fingerprint — looks like a normal Linux Chrome install
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Playwright anti-detection args
BROWSER_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--no-first-run",
    "--no-default-browser-check",
]

# ══════════════════════════════════════════════════════════════════
# Handle Configuration
# ══════════════════════════════════════════════════════════════════

def load_handles_from_env() -> list:
    """
    Load X handle configs from environment variables.
    
    Set XFEED_HANDLES as a JSON array:
        export XFEED_HANDLES='[
            {"username": "myhandle", "role": "primary", "alias": "main"},
            {"username": "myalt", "role": "alt", "alias": "alt"},
            {"username": "mybotaccount", "role": "bot", "alias": "bot"}
        ]'
    
    Or use the simple single-account var:
        export XFEED_USERNAME=myhandle
    
    Roles:
        "primary" — Your main account. Posts/replies + Following tab get scraped.
        "alt"     — Backup account. Posts/replies scraped.
        "bot"     — Your AI's account. Checks notifications/mentions for @tags.
    
    Returns: list of handle dicts
    """
    handles = []

    # Try JSON config first
    handles_json = os.environ.get("XFEED_HANDLES", "")
    if handles_json:
        try:
            handles = json.loads(handles_json)
            return handles
        except json.JSONDecodeError:
            log.warning("XFEED_HANDLES JSON parse failed, trying XFEED_USERNAME")

    # Simple single-account fallback
    username = os.environ.get("XFEED_USERNAME", "")
    if username:
        handles.append({
            "username": username,
            "role": "primary",
            "alias": "main",
        })

    return handles


# ══════════════════════════════════════════════════════════════════
# Scraper Class
# ══════════════════════════════════════════════════════════════════

class XFeedScraper:
    """
    Reads X/Twitter timelines using a persistent Playwright browser session.
    No API keys needed — just a logged-in browser profile.
    
    Usage:
        scraper = XFeedScraper(handles=[
            {"username": "myhandle", "role": "primary", "alias": "main"}
        ])
        
        # First time: log in manually
        await scraper.setup_login()
        
        # Then scrape whenever you want
        results = await scraper.check_feed()
        print(json.dumps(results, indent=2))
    """

    def __init__(
        self,
        handles: Optional[list] = None,
        profile_dir: str = DEFAULT_PROFILE_DIR,
        max_posts: int = DEFAULT_MAX_POSTS,
        scroll_pause: float = DEFAULT_SCROLL_PAUSE,
        max_scrolls: int = DEFAULT_MAX_SCROLLS,
        page_timeout: int = DEFAULT_PAGE_TIMEOUT,
        cooldown_minutes: int = DEFAULT_COOLDOWN_MINUTES,
        user_agent: str = DEFAULT_USER_AGENT,
        on_posts: Optional[Callable] = None,
        webhook_url: Optional[str] = None,
    ):
        """
        Args:
            handles: List of account dicts. If None, loads from env vars.
            profile_dir: Where to store the persistent browser profile.
            max_posts: Max posts to collect per feed check.
            scroll_pause: Seconds to wait between page scrolls.
            max_scrolls: Max scroll attempts per page.
            page_timeout: Page load timeout in milliseconds.
            cooldown_minutes: Min time between feed checks (0 to disable).
            user_agent: Browser user agent string.
            on_posts: Optional async callback: async def handler(posts: list, metadata: dict)
            webhook_url: Optional URL to POST results to after each check.
        """
        self.handles = handles or load_handles_from_env()
        self.profile_dir = profile_dir
        self.max_posts = max_posts
        self.scroll_pause = scroll_pause
        self.max_scrolls = max_scrolls
        self.page_timeout = page_timeout
        self.cooldown_minutes = cooldown_minutes
        self.user_agent = user_agent
        self.on_posts = on_posts
        self.webhook_url = webhook_url

        self._checking = False
        self._last_check = 0

        # Ensure profile dir exists
        Path(self.profile_dir).mkdir(parents=True, exist_ok=True)

        # Check Playwright availability
        self._pw_available = False
        try:
            from playwright.async_api import async_playwright
            self._pw_available = True
        except ImportError:
            log.warning(
                "Playwright not installed. Run: pip install playwright && playwright install chromium"
            )

    # ── Properties ────────────────────────────────────────────────

    @property
    def is_available(self) -> bool:
        return self._pw_available

    @property
    def is_on_cooldown(self) -> bool:
        if self.cooldown_minutes <= 0:
            return False
        elapsed = time.time() - self._last_check
        return elapsed < (self.cooldown_minutes * 60)

    @property
    def cooldown_remaining(self) -> float:
        if not self.is_on_cooldown:
            return 0
        elapsed = time.time() - self._last_check
        return round((self.cooldown_minutes * 60 - elapsed) / 60, 1)

    @property
    def has_session(self) -> bool:
        """Check if a browser profile with saved session exists."""
        try:
            return any(Path(self.profile_dir).iterdir())
        except Exception:
            return False

    # ── Handle Management ─────────────────────────────────────────

    def add_handle(self, username: str, role: str = "alt", alias: Optional[str] = None) -> dict:
        """Add or update an X handle to scan."""
        alias = alias or username
        for h in self.handles:
            if h["alias"] == alias:
                h["username"] = username
                h["role"] = role
                return h

        new_handle = {"username": username, "role": role, "alias": alias}
        self.handles.append(new_handle)
        return new_handle

    def remove_handle(self, alias: str) -> bool:
        before = len(self.handles)
        self.handles = [h for h in self.handles if h["alias"] != alias]
        return len(self.handles) < before

    def list_handles(self) -> list:
        return list(self.handles)

    def _get_handles_by_role(self, role: str) -> list:
        return [h for h in self.handles if h.get("role") == role]

    def _get_primary_username(self) -> str:
        for h in self.handles:
            if h.get("role") == "primary":
                return h["username"]
        if self.handles:
            return self.handles[0]["username"]
        return ""

    # ── Browser Helpers ───────────────────────────────────────────

    def _clean_lock(self):
        """Remove stale Chromium SingletonLock file."""
        lock = os.path.join(self.profile_dir, "SingletonLock")
        if os.path.exists(lock):
            os.remove(lock)

    async def _launch_context(self, pw, headless: bool = True):
        """Launch a persistent browser context with anti-detection settings."""
        self._clean_lock()
        return await pw.chromium.launch_persistent_context(
            user_data_dir=self.profile_dir,
            headless=headless,
            viewport={"width": 1280, "height": 900},
            user_agent=self.user_agent,
            args=BROWSER_ARGS,
            ignore_default_args=["--enable-automation"],
        )

    # ── Setup: First-Time Login ───────────────────────────────────

    async def setup_login(self) -> dict:
        """
        Opens a HEADED browser so you can manually log into X.
        Log into all your accounts using X's account switcher.
        Close the browser window when done — cookies are saved automatically.
        
        Returns:
            dict with status
        """
        if not self._pw_available:
            return {"status": "error", "message": "Playwright not installed"}

        log.info("Opening browser for X login...")
        print("\n" + "=" * 60)
        print("  X Feed Scraper — First-Time Setup")
        print("=" * 60)
        print("\n  A browser window will open to x.com/login")
        print("  → Log into your account(s)")
        print("  → Use X's account switcher to add multiple accounts")
        print("  → Close the browser window when done")
        print("  → Your session will be saved for future headless use\n")

        try:
            from playwright.async_api import async_playwright
            pw = await async_playwright().start()
            context = await self._launch_context(pw, headless=False)

            page = await context.new_page()
            await page.goto("https://x.com/login", timeout=self.page_timeout)

            # Wait for user to close the browser
            try:
                while True:
                    pages = context.pages
                    if not pages:
                        break
                    all_closed = True
                    for p in pages:
                        try:
                            _ = p.url
                            all_closed = False
                        except Exception:
                            pass
                    if all_closed:
                        break
                    await asyncio.sleep(1)
            except Exception:
                pass

            try:
                await context.close()
            except Exception:
                pass
            await pw.stop()

            if self.has_session:
                print("\n  ✅ Session saved! You can now use headless mode.\n")
                return {"status": "ok", "message": "X session saved."}
            else:
                print("\n  ❌ No session data saved. Try again.\n")
                return {"status": "error", "message": "No session data saved."}

        except Exception as e:
            log.error(f"Setup failed: {e}")
            return {"status": "error", "message": str(e)}

    # ── Core: Check Feed ──────────────────────────────────────────

    async def check_feed(self) -> dict:
        """
        Main feed check. Scans all configured handles in a single browser session:
        
        1. For each primary/alt handle: scrape posts & replies
        2. For bot handles: check notifications/mentions
        3. Scrape the Following tab
        4. Fire callbacks/webhooks with results
        
        Returns:
            dict with all scraped posts, metadata, and timing info
        """
        if not self._pw_available:
            return {"status": "error", "message": "Playwright not installed"}

        if self._checking:
            return {"status": "busy", "message": "Already checking feed"}

        if self.is_on_cooldown:
            return {"status": "cooldown", "minutes_remaining": self.cooldown_remaining}

        if not self.has_session:
            return {"status": "error", "message": "No session found. Run setup first."}

        self._checking = True
        self._last_check = time.time()
        session_start = time.time()

        result = {
            "status": "ok",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "handles_checked": [],
            "posts": [],
            "mentions": [],
            "posts_found": 0,
            "duration_seconds": 0,
        }

        try:
            from playwright.async_api import async_playwright
            pw = await async_playwright().start()
            context = await self._launch_context(pw)
            page = await context.new_page()
            all_posts = []

            # ── Phase 1: Your posts & replies ─────────────────────
            user_handles = [h for h in self.handles if h.get("role") in ("primary", "alt")]
            for handle_info in user_handles:
                username = handle_info["username"]
                alias = handle_info.get("alias", username)
                if not username:
                    continue

                log.info(f"Scraping @{username} posts & replies...")
                await page.goto(
                    f"https://x.com/{username}/with_replies",
                    timeout=self.page_timeout,
                )
                await page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(3)

                # Check login status
                if "login" in page.url.lower():
                    await context.close()
                    await pw.stop()
                    self._checking = False
                    return {"status": "error", "message": "Session expired. Run setup again."}

                for scroll_i in range(3):
                    new_posts = await self._extract_posts(page)
                    for p in new_posts:
                        p["source"] = f"posts_replies"
                        p["source_handle"] = username
                        p["source_alias"] = alias
                        if not self._is_duplicate(p, all_posts):
                            all_posts.append(p)

                    if len(all_posts) >= 15:
                        break

                    await page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
                    await asyncio.sleep(self.scroll_pause)

                result["handles_checked"].append(username)
                log.info(f"  → {len(all_posts)} posts from @{username}")

            # ── Phase 2: Bot account mentions ─────────────────────
            bot_handles = [h for h in self.handles if h.get("role") == "bot"]
            for handle_info in bot_handles:
                username = handle_info["username"]
                if not username:
                    continue

                log.info(f"Checking @{username} mentions...")

                # Try to switch to bot account via X's account switcher
                await self._switch_account(page, username)

                await page.goto(
                    "https://x.com/notifications/mentions",
                    timeout=self.page_timeout,
                )
                await page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(3)

                if "login" in page.url.lower():
                    log.warning(f"Session expired for @{username}, skipping")
                    result["handles_checked"].append(f"{username} (FAILED)")
                    continue

                mention_posts = []
                seen_hashes = set()
                for scroll_i in range(3):
                    new_posts = await self._extract_posts(page)
                    for p in new_posts:
                        h = hash(p.get("text", "")[:100])
                        if h not in seen_hashes:
                            seen_hashes.add(h)
                            mention_posts.append(p)
                    if scroll_i < 2:
                        await page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
                        await asyncio.sleep(self.scroll_pause)

                for p in mention_posts[:15]:
                    p["source"] = "mention"
                    p["source_handle"] = username
                    result["mentions"].append({
                        "from": p.get("handle", "unknown"),
                        "text": p.get("text", "")[:500],
                        "tweet_id": p.get("tweet_id", ""),
                        "tweet_url": p.get("tweet_url", ""),
                        "timestamp": p.get("timestamp", ""),
                    })
                    if not self._is_duplicate(p, all_posts):
                        all_posts.append(p)

                result["handles_checked"].append(f"{username} (mentions)")
                log.info(f"  → {len(mention_posts)} mentions for @{username}")

                # Switch back to primary
                primary = self._get_primary_username()
                if primary:
                    await self._switch_account(page, primary)

            # ── Phase 3: Following tab ────────────────────────────
            log.info("Scraping Following tab...")
            await page.goto("https://x.com/home", timeout=self.page_timeout)
            await page.wait_for_load_state("domcontentloaded")
            await asyncio.sleep(2)

            try:
                following_tab = await page.query_selector(
                    'a[href="/home"][role="tab"]:has-text("Following")'
                )
                if not following_tab:
                    following_tab = await page.query_selector('text="Following"')
                if following_tab:
                    await following_tab.click()
                    await asyncio.sleep(3)
            except Exception as e:
                log.warning(f"Couldn't switch to Following tab: {e}")

            following_start = len(all_posts)
            for scroll_i in range(self.max_scrolls):
                new_posts = await self._extract_posts(page)
                for p in new_posts:
                    p["source"] = "following"
                    if not self._is_duplicate(p, all_posts):
                        all_posts.append(p)

                if len(all_posts) >= self.max_posts:
                    break

                await page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
                await asyncio.sleep(self.scroll_pause)

            log.info(f"  → {len(all_posts) - following_start} posts from Following tab")

            # ── Cleanup ───────────────────────────────────────────
            await context.close()
            await pw.stop()

            result["posts"] = all_posts
            result["posts_found"] = len(all_posts)
            result["duration_seconds"] = round(time.time() - session_start, 1)

            # ── Callbacks ─────────────────────────────────────────
            if self.on_posts and all_posts:
                try:
                    if asyncio.iscoroutinefunction(self.on_posts):
                        await self.on_posts(all_posts, result)
                    else:
                        self.on_posts(all_posts, result)
                except Exception as e:
                    log.warning(f"Callback failed: {e}")

            if self.webhook_url and all_posts:
                await self._fire_webhook(result)

            log.info(
                f"Feed check complete: {result['posts_found']} posts, "
                f"{len(result.get('mentions', []))} mentions, "
                f"{result['duration_seconds']}s"
            )

        except Exception as e:
            log.error(f"Feed check failed: {e}", exc_info=True)
            result["status"] = "error"
            result["message"] = str(e)
        finally:
            self._checking = False

        return result

    # ── Check Specific User ───────────────────────────────────────

    async def check_user(self, username: str, max_posts: int = 10) -> dict:
        """
        Scrape a specific user's timeline.
        
        Args:
            username: X handle without @
            max_posts: Max posts to scrape
            
        Returns:
            dict with posts
        """
        if not self._pw_available:
            return {"status": "error", "message": "Playwright not installed"}
        if self._checking:
            return {"status": "busy"}
        if not self.has_session:
            return {"status": "error", "message": "No session. Run setup first."}

        self._checking = True
        result = {"status": "ok", "user": username, "posts": [], "posts_found": 0}

        try:
            from playwright.async_api import async_playwright
            pw = await async_playwright().start()
            context = await self._launch_context(pw)
            page = await context.new_page()

            await page.goto(f"https://x.com/{username}", timeout=self.page_timeout)
            await page.wait_for_load_state("domcontentloaded")
            await asyncio.sleep(3)

            if "login" in page.url.lower():
                await context.close()
                await pw.stop()
                self._checking = False
                return {"status": "error", "message": "Session expired. Run setup again."}

            all_posts = []
            for scroll_i in range(3):
                new_posts = await self._extract_posts(page)
                for p in new_posts:
                    if not self._is_duplicate(p, all_posts):
                        all_posts.append(p)
                if len(all_posts) >= max_posts:
                    break
                await page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
                await asyncio.sleep(self.scroll_pause)

            result["posts"] = all_posts[:max_posts]
            result["posts_found"] = len(result["posts"])

            await context.close()
            await pw.stop()

        except Exception as e:
            result["status"] = "error"
            result["message"] = str(e)
        finally:
            self._checking = False

        return result

    # ── Check Notifications ───────────────────────────────────────

    async def check_notifications(self) -> dict:
        """
        Check your X notifications (likes, reposts, replies, follows).
        
        Returns:
            dict with categorized notifications
        """
        if not self._pw_available:
            return {"status": "error", "message": "Playwright not installed"}
        if self._checking:
            return {"status": "busy"}
        if not self.has_session:
            return {"status": "error", "message": "No session. Run setup first."}

        self._checking = True
        result = {"status": "ok", "notifications": [], "count": 0}

        try:
            from playwright.async_api import async_playwright
            pw = await async_playwright().start()
            context = await self._launch_context(pw)
            page = await context.new_page()

            await page.goto("https://x.com/notifications", timeout=self.page_timeout)
            await page.wait_for_load_state("domcontentloaded")
            await asyncio.sleep(3)

            notifs = await page.evaluate("""
                () => {
                    const articles = document.querySelectorAll('article');
                    const results = [];
                    for (const article of articles) {
                        const text = article.textContent || '';
                        if (text.trim()) {
                            results.push({
                                text: text.substring(0, 500),
                                type: text.includes('liked') ? 'like' :
                                      text.includes('reposted') ? 'repost' :
                                      text.includes('replied') ? 'reply' :
                                      text.includes('mentioned') ? 'mention' :
                                      text.includes('followed') ? 'follow' : 'other'
                            });
                        }
                    }
                    return results.slice(0, 20);
                }
            """)

            result["notifications"] = notifs or []
            result["count"] = len(notifs or [])

            await context.close()
            await pw.stop()

        except Exception as e:
            result["status"] = "error"
            result["message"] = str(e)
        finally:
            self._checking = False

        return result

    # ── Post Reply ────────────────────────────────────────────────

    async def post_reply(self, tweet_url: str, reply_text: str,
                         use_bot_account: bool = True) -> dict:
        """
        Post a reply to a tweet.
        
        Args:
            tweet_url: Full URL of the tweet to reply to
            reply_text: Text to post as reply (max 280 chars)
            use_bot_account: If True, switch to bot account before replying
            
        Returns:
            dict with status and details
        """
        if not self._pw_available:
            return {"status": "error", "message": "Playwright not installed"}
        if self._checking:
            return {"status": "busy"}
        if not tweet_url:
            return {"status": "error", "message": "tweet_url required"}
        if not reply_text or not reply_text.strip():
            return {"status": "error", "message": "reply_text required"}
        if len(reply_text) > 280:
            return {"status": "error", "message": f"Too long ({len(reply_text)}/280 chars)"}
        if not self.has_session:
            return {"status": "error", "message": "No session. Run setup first."}

        self._checking = True
        result = {"status": "ok", "tweet_url": tweet_url, "reply_text": reply_text, "posted": False}

        try:
            from playwright.async_api import async_playwright
            pw = await async_playwright().start()
            context = await self._launch_context(pw)
            page = await context.new_page()

            # Switch to bot account if configured
            if use_bot_account:
                bot = next((h for h in self.handles if h.get("role") == "bot"), None)
                if bot:
                    await page.goto("https://x.com/home", timeout=self.page_timeout)
                    await page.wait_for_load_state("domcontentloaded")
                    await asyncio.sleep(2)
                    await self._switch_account(page, bot["username"])

            # Navigate to the tweet
            await page.goto(tweet_url, timeout=self.page_timeout)
            await page.wait_for_load_state("domcontentloaded")
            await asyncio.sleep(3)

            if "login" in page.url.lower():
                await context.close()
                await pw.stop()
                self._checking = False
                return {"status": "error", "message": "Session expired. Run setup again."}

            # Find reply box
            reply_box = await page.query_selector('[data-testid="tweetTextarea_0"]')
            if not reply_box:
                reply_btn = await page.query_selector('[data-testid="reply"]')
                if reply_btn:
                    await reply_btn.click()
                    await asyncio.sleep(2)
                    reply_box = await page.query_selector('[data-testid="tweetTextarea_0"]')

            if not reply_box:
                reply_box = await page.query_selector(
                    '[role="textbox"][data-testid="tweetTextarea_0"]'
                )

            if not reply_box:
                await context.close()
                await pw.stop()
                self._checking = False
                return {"status": "error", "message": "Could not find reply input"}

            # Type and post
            await reply_box.click()
            await asyncio.sleep(0.5)
            await reply_box.fill(reply_text)
            await asyncio.sleep(1)

            post_btn = await page.query_selector('[data-testid="tweetButtonInline"]')
            if not post_btn:
                post_btn = await page.query_selector('[data-testid="tweetButton"]')

            if not post_btn:
                await context.close()
                await pw.stop()
                self._checking = False
                return {"status": "error", "message": "Could not find Post button"}

            await post_btn.click()
            await asyncio.sleep(3)

            result["posted"] = True
            result["posted_at"] = datetime.now(timezone.utc).isoformat()
            log.info(f"Reply posted to {tweet_url}")

            await context.close()
            await pw.stop()

        except Exception as e:
            log.error(f"Reply failed: {e}", exc_info=True)
            result["status"] = "error"
            result["message"] = str(e)
            result["posted"] = False
        finally:
            self._checking = False

        return result

    # ── DOM Extraction ────────────────────────────────────────────

    async def _extract_posts(self, page) -> list:
        """Extract post data from the current page using DOM queries."""
        try:
            posts = await page.evaluate("""
                () => {
                    const articles = document.querySelectorAll('article[data-testid="tweet"]');
                    const results = [];
                    
                    for (const article of articles) {
                        try {
                            // Author + handle
                            const userLinks = article.querySelectorAll('a[role="link"]');
                            let author = '', handle = '';
                            for (const link of userLinks) {
                                const href = link.getAttribute('href') || '';
                                if (href.match(/^\\/[^/]+$/) && !href.includes('/status/')) {
                                    handle = href.replace('/', '');
                                    const nameEl = link.querySelector('span');
                                    if (nameEl) author = nameEl.textContent;
                                    break;
                                }
                            }
                            
                            // Post text
                            const textEl = article.querySelector('[data-testid="tweetText"]');
                            const text = textEl ? textEl.textContent : '';
                            if (!text.trim()) continue;
                            
                            // Engagement
                            const metricsGroup = article.querySelector('[role="group"]');
                            let replies = 0, reposts = 0, likes = 0, views = 0;
                            if (metricsGroup) {
                                const buttons = metricsGroup.querySelectorAll('button');
                                buttons.forEach((btn, i) => {
                                    const val = parseInt(btn.textContent.replace(/[^0-9]/g, '')) || 0;
                                    if (i === 0) replies = val;
                                    if (i === 1) reposts = val;
                                    if (i === 2) likes = val;
                                    if (i === 3) views = val;
                                });
                            }
                            
                            // Timestamp
                            const timeEl = article.querySelector('time');
                            const timestamp = timeEl ? timeEl.getAttribute('datetime') : '';
                            
                            // Repost?
                            const ctx = article.querySelector('[data-testid="socialContext"]');
                            const is_repost = ctx ? 
                                ctx.textContent.toLowerCase().includes('repost') : false;
                            
                            // Media
                            const has_image = !!article.querySelector('[data-testid="tweetPhoto"]');
                            const has_video = !!article.querySelector('[data-testid="videoPlayer"]');
                            
                            // Tweet ID + URL
                            let tweet_id = '', tweet_url = '';
                            const statusLinks = article.querySelectorAll('a[href*="/status/"]');
                            for (const sl of statusLinks) {
                                const href = sl.getAttribute('href') || '';
                                const match = href.match(/\\/status\\/(\\d+)/);
                                if (match) {
                                    tweet_id = match[1];
                                    tweet_url = 'https://x.com' + href;
                                    break;
                                }
                            }
                            
                            results.push({
                                author, handle, text: text.substring(0, 1000),
                                tweet_id, tweet_url,
                                replies, reposts, likes, views,
                                timestamp, is_repost, has_image, has_video,
                            });
                        } catch (e) {}
                    }
                    return results;
                }
            """)
            return posts or []
        except Exception as e:
            log.warning(f"DOM extraction failed: {e}")
            return []

    # ── Account Switching ─────────────────────────────────────────

    async def _switch_account(self, page, target_username: str):
        """Try to switch to a different X account via the account switcher UI."""
        try:
            menu = await page.query_selector('[data-testid="SideNav_AccountSwitcher_Button"]')
            if not menu:
                log.warning("No account switcher button found")
                return

            await menu.click()
            await asyncio.sleep(1.5)

            option = await page.query_selector(
                f'[data-testid="AccountSwitcher_Account_{target_username}"]'
            )
            if not option:
                option = await page.query_selector(
                    f'a[href="/{target_username}"], span:has-text("@{target_username}")'
                )

            if option:
                await option.click()
                await asyncio.sleep(3)
                log.info(f"Switched to @{target_username}")
            else:
                await page.keyboard.press("Escape")
                log.warning(f"Couldn't find @{target_username} in switcher")

        except Exception as e:
            log.warning(f"Account switch failed: {e}")

    # ── Dedup Helper ──────────────────────────────────────────────

    @staticmethod
    def _is_duplicate(post: dict, existing: list) -> bool:
        content_hash = hash(post.get("text", "")[:100])
        return any(hash(p.get("text", "")[:100]) == content_hash for p in existing)

    # ── Webhook ───────────────────────────────────────────────────

    async def _fire_webhook(self, result: dict):
        """POST results to a webhook URL."""
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.webhook_url,
                    json=result,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        log.info(f"Webhook delivered to {self.webhook_url}")
                    else:
                        log.warning(f"Webhook returned {resp.status}")
        except ImportError:
            # Fallback without aiohttp — use urllib
            import urllib.request
            req = urllib.request.Request(
                self.webhook_url,
                data=json.dumps(result).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    log.info(f"Webhook delivered ({resp.status})")
            except Exception as e:
                log.warning(f"Webhook failed: {e}")
        except Exception as e:
            log.warning(f"Webhook failed: {e}")


# ══════════════════════════════════════════════════════════════════
# CLI Interface
# ══════════════════════════════════════════════════════════════════

def cli():
    parser = argparse.ArgumentParser(
        description="X Feed Scraper — Read X/Twitter without API keys",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s setup                          # First-time login (opens browser)
  %(prog)s check                          # Check your feed
  %(prog)s check --webhook http://...     # Check + POST results
  %(prog)s check --json                   # Output raw JSON
  %(prog)s user elonmusk                  # Check specific user
  %(prog)s user elonmusk --max 20         # More posts
  %(prog)s notifications                  # Check notifications
  %(prog)s reply URL "text"               # Reply to a tweet
  %(prog)s handles                        # List configured handles

Environment Variables:
  XFEED_USERNAME    Single account (simple setup)
  XFEED_HANDLES     JSON array of accounts (multi-account)
  XFEED_PROFILE     Browser profile directory
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # setup
    subparsers.add_parser("setup", help="Open browser for manual X login")

    # check
    check_p = subparsers.add_parser("check", help="Check your feed")
    check_p.add_argument("--webhook", help="URL to POST results to")
    check_p.add_argument("--json", action="store_true", help="Output raw JSON")
    check_p.add_argument("--no-cooldown", action="store_true", help="Ignore cooldown")
    check_p.add_argument("--max", type=int, default=30, help="Max posts (default: 30)")

    # user
    user_p = subparsers.add_parser("user", help="Check a specific user's timeline")
    user_p.add_argument("username", help="X handle (without @)")
    user_p.add_argument("--max", type=int, default=10, help="Max posts (default: 10)")
    user_p.add_argument("--json", action="store_true", help="Output raw JSON")

    # notifications
    notif_p = subparsers.add_parser("notifications", help="Check notifications")
    notif_p.add_argument("--json", action="store_true", help="Output raw JSON")

    # reply
    reply_p = subparsers.add_parser("reply", help="Reply to a tweet")
    reply_p.add_argument("tweet_url", help="URL of the tweet to reply to")
    reply_p.add_argument("text", help="Reply text (max 280 chars)")
    reply_p.add_argument("--no-switch", action="store_true",
                         help="Don't switch to bot account")

    # handles
    subparsers.add_parser("handles", help="List configured handles")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
    )

    # Profile dir from env or default
    profile_dir = os.environ.get("XFEED_PROFILE", DEFAULT_PROFILE_DIR)

    # Build scraper
    scraper_kwargs = {"profile_dir": profile_dir}

    if args.command == "check":
        if args.webhook:
            scraper_kwargs["webhook_url"] = args.webhook
        if args.no_cooldown:
            scraper_kwargs["cooldown_minutes"] = 0
        scraper_kwargs["max_posts"] = args.max

    scraper = XFeedScraper(**scraper_kwargs)

    # Dispatch
    if args.command == "setup":
        result = asyncio.run(scraper.setup_login())
        if result["status"] != "ok":
            print(f"Error: {result.get('message', 'Unknown error')}")
            sys.exit(1)

    elif args.command == "check":
        if not scraper.handles:
            print("No handles configured!")
            print("Set XFEED_USERNAME or XFEED_HANDLES environment variable.")
            print('Example: export XFEED_USERNAME="myhandle"')
            sys.exit(1)

        result = asyncio.run(scraper.check_feed())

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            _print_feed_results(result)

    elif args.command == "user":
        max_posts = args.max
        result = asyncio.run(scraper.check_user(args.username, max_posts))

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            _print_user_results(result)

    elif args.command == "notifications":
        result = asyncio.run(scraper.check_notifications())

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            _print_notifications(result)

    elif args.command == "reply":
        result = asyncio.run(
            scraper.post_reply(args.tweet_url, args.text, not args.no_switch)
        )
        if result.get("posted"):
            print(f"✅ Reply posted to {args.tweet_url}")
        else:
            print(f"❌ {result.get('message', 'Failed')}")
            sys.exit(1)

    elif args.command == "handles":
        handles = scraper.list_handles()
        if not handles:
            print("No handles configured.")
            print('Set XFEED_USERNAME="myhandle" or XFEED_HANDLES as JSON.')
        else:
            print(f"\nConfigured handles ({len(handles)}):\n")
            for h in handles:
                print(f"  @{h['username']:20s}  role={h.get('role','?'):10s}  alias={h.get('alias','?')}")
            print()


# ── CLI Output Formatters ─────────────────────────────────────────

def _print_feed_results(result: dict):
    if result["status"] != "ok":
        print(f"\n❌ {result.get('message', result['status'])}\n")
        return

    posts = result.get("posts", [])
    mentions = result.get("mentions", [])

    print(f"\n{'=' * 60}")
    print(f"  X Feed — {result.get('posts_found', 0)} posts, "
          f"{len(mentions)} mentions, {result.get('duration_seconds', 0)}s")
    print(f"  Handles: {', '.join(result.get('handles_checked', []))}")
    print(f"{'=' * 60}\n")

    # Group by source
    own_posts = [p for p in posts if p.get("source") == "posts_replies"]
    following = [p for p in posts if p.get("source") == "following"]

    if own_posts:
        print("── Your Posts & Replies ────────────────────────────")
        for p in own_posts[:10]:
            _print_post(p)

    if following:
        print("── Following Tab ──────────────────────────────────")
        for p in following[:10]:
            _print_post(p)

    if mentions:
        print("── Mentions ───────────────────────────────────────")
        for m in mentions[:10]:
            print(f"  @{m.get('from', '?')}: {m.get('text', '')[:200]}")
            print()


def _print_user_results(result: dict):
    if result["status"] != "ok":
        print(f"\n❌ {result.get('message', result['status'])}\n")
        return

    print(f"\n{'=' * 60}")
    print(f"  @{result.get('user', '?')} — {result.get('posts_found', 0)} posts")
    print(f"{'=' * 60}\n")

    for p in result.get("posts", []):
        _print_post(p)


def _print_notifications(result: dict):
    if result["status"] != "ok":
        print(f"\n❌ {result.get('message', result['status'])}\n")
        return

    print(f"\n{'=' * 60}")
    print(f"  Notifications — {result.get('count', 0)} items")
    print(f"{'=' * 60}\n")

    for n in result.get("notifications", []):
        icon = {"like": "❤️", "repost": "🔁", "reply": "💬",
                "mention": "📢", "follow": "👤"}.get(n.get("type", ""), "•")
        print(f"  {icon} [{n.get('type', '?')}] {n.get('text', '')[:200]}")
        print()


def _print_post(post: dict):
    handle = post.get("handle", "?")
    text = post.get("text", "")[:300]
    r, rp, l, v = post.get("replies", 0), post.get("reposts", 0), \
                   post.get("likes", 0), post.get("views", 0)
    repost = " 🔁REPOST" if post.get("is_repost") else ""
    media = ""
    if post.get("has_image"):
        media += " 🖼️"
    if post.get("has_video"):
        media += " 🎥"

    print(f"  @{handle}{repost}{media}")
    print(f"  {text}")
    print(f"  💬{r}  🔁{rp}  ❤️{l}  👁️{v}")
    if post.get("tweet_url"):
        print(f"  {post['tweet_url']}")
    print()


# ══════════════════════════════════════════════════════════════════
# Entry Point
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    cli()
