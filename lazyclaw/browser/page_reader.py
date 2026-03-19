"""Page Reader — Lightweight page scraping with JS extractors.

Extracted from LazyTasker. Uses Playwright directly (no browser-use overhead)
to load pages, extract clean content via injected JavaScript, and optionally
analyze with the user's configured LLM via LLMRouter.

Cost: ~$0.001/page vs ~$0.30/page with browser-use agent.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ── JS Extractors ────────────────────────────────────────────────────────

JS_GENERIC = """
() => {
    const remove = ['script','style','noscript','iframe','svg',
        'nav','footer','header','.ad,.ads,.advertisement',
        '[class*="cookie"],[class*="consent"],[class*="popup"]',
        '[class*="sidebar"],[class*="banner"],[role="banner"]',
        '[aria-hidden="true"]'];
    remove.forEach(sel => {
        try { document.querySelectorAll(sel).forEach(el => el.remove()); } catch(e) {}
    });
    const main = document.querySelector('main, article, [role="main"], .content, #content')
        || document.body;
    const lines = [];
    const walk = (node) => {
        if (!node) return;
        if (node.nodeType === 3) {
            const t = node.textContent.trim();
            if (t) lines.push(t);
            return;
        }
        if (node.nodeType !== 1) return;
        const style = window.getComputedStyle(node);
        if (style.display === 'none' || style.visibility === 'hidden') return;
        const tag = node.tagName;
        if (['H1','H2','H3','H4','H5','H6'].includes(tag)) {
            lines.push('\\n## ' + node.textContent.trim());
        } else if (tag === 'A' && node.href && node.textContent.trim()) {
            lines.push('[' + node.textContent.trim() + '](' + node.href + ')');
        } else if (tag === 'LI') {
            lines.push('- ' + node.textContent.trim());
        } else {
            for (const child of node.childNodes) walk(child);
            if (['P','DIV','BR','TR','SECTION'].includes(tag)) lines.push('');
        }
    };
    walk(main);
    let text = lines.join('\\n').replace(/\\n{3,}/g, '\\n\\n').trim();
    if (text.length > 6000) text = text.substring(0, 6000) + '\\n... [truncated]';
    const navLinks = [];
    document.querySelectorAll('nav a, [role="navigation"] a, .menu a, .sidebar a, .breadcrumb a, [class*="pagination"] a').forEach((a, i) => {
        if (i >= 20 || !a.textContent.trim() || !a.href) return;
        navLinks.push('- [' + a.textContent.trim() + '](' + a.href + ')');
    });
    const imgCount = document.querySelectorAll('main img, article img, .content img, #content img').length
        || document.querySelectorAll('img').length;
    const metaParts = [];
    const author = document.querySelector('meta[name="author"]')?.content
        || document.querySelector('[rel="author"], .author, [class*="author"]')?.textContent?.trim();
    const pubDate = document.querySelector('meta[property="article:published_time"]')?.content
        || document.querySelector('time[datetime]')?.getAttribute('datetime')
        || document.querySelector('time')?.textContent?.trim();
    if (author) metaParts.push('Author: ' + author);
    if (pubDate) metaParts.push('Published: ' + pubDate);
    return {
        title: document.title || '',
        text: text,
        url: location.href,
        type: 'generic',
        links: navLinks.join('\\n'),
        images: imgCount,
        meta: metaParts.join('\\n')
    };
}
"""

JS_SEARCH = """
() => {
    const results = [];
    document.querySelectorAll('#search .g, #rso .g').forEach((g, i) => {
        if (i >= 10) return;
        const a = g.querySelector('a[href]');
        const title = g.querySelector('h3');
        const snippet = g.querySelector('.VwiC3b, [data-sncf], [style*="-webkit-line-clamp"]');
        if (a && title) {
            results.push({
                rank: i + 1, title: title.textContent.trim(),
                url: a.href, snippet: snippet ? snippet.textContent.trim() : ''
            });
        }
    });
    if (!results.length) {
        document.querySelectorAll('[data-result], .result').forEach((r, i) => {
            if (i >= 10) return;
            const a = r.querySelector('a[href]');
            const snippet = r.querySelector('.result__snippet, .snippet');
            if (a) results.push({
                rank: i + 1, title: a.textContent.trim(),
                url: a.href, snippet: snippet ? snippet.textContent.trim() : ''
            });
        });
    }
    if (!results.length) {
        document.querySelectorAll('#b_results .b_algo').forEach((r, i) => {
            if (i >= 10) return;
            const a = r.querySelector('h2 a');
            const snippet = r.querySelector('.b_caption p');
            if (a) results.push({
                rank: i + 1, title: a.textContent.trim(),
                url: a.href, snippet: snippet ? snippet.textContent.trim() : ''
            });
        });
    }
    const text = results.map(r =>
        r.rank + '. ' + r.title + '\\n   ' + r.url +
        (r.snippet ? '\\n   ' + r.snippet : '')
    ).join('\\n\\n');
    return {
        title: document.title,
        text: text || 'No search results found on this page.',
        url: location.href, type: 'search',
        result_count: results.length
    };
}
"""

JS_ARTICLE = """
() => {
    const article = document.querySelector('article') || document.querySelector('main') || document.body;
    article.querySelectorAll('script,style,nav,footer,.ad,.ads,aside,[class*="related"]').forEach(el => el.remove());
    const title = document.querySelector('h1')?.textContent?.trim() || document.title || '';
    const meta_desc = document.querySelector('meta[name="description"]')?.content || '';
    const meta_kw = document.querySelector('meta[name="keywords"]')?.content || '';
    const headings = [];
    article.querySelectorAll('h1,h2,h3,h4').forEach(h => {
        const t = h.textContent.trim();
        if (t) headings.push({level: parseInt(h.tagName[1]), text: t});
    });
    const paragraphs = [];
    article.querySelectorAll('p').forEach(p => {
        const t = p.textContent.trim();
        if (t.length > 20) paragraphs.push(t);
    });
    const full_text = paragraphs.join('\\n\\n');
    const word_count = full_text.split(/\\s+/).length;
    let text = '# ' + title + '\\n\\n';
    if (meta_desc) text += 'Description: ' + meta_desc + '\\n';
    if (meta_kw) text += 'Keywords: ' + meta_kw + '\\n';
    text += 'Words: ' + word_count + '\\n\\n';
    text += headings.map(h => '#'.repeat(h.level) + ' ' + h.text).join('\\n') + '\\n\\n';
    text += full_text;
    if (text.length > 6000) text = text.substring(0, 6000) + '\\n... [truncated]';
    return {
        title: title, text: text, url: location.href, type: 'article',
        word_count: word_count, meta_keywords: meta_kw
    };
}
"""

JS_EMAIL = """
() => {
    const emails = [];
    document.querySelectorAll('tr.zA, [role="row"]').forEach((row, i) => {
        if (i >= 15) return;
        const sender = row.querySelector('.yW .bA4, [email], .from')?.textContent?.trim() || '';
        const subject = row.querySelector('.bog, .subject, [data-thread-id] span')?.textContent?.trim() || '';
        const snippet = row.querySelector('.y2, .snippet')?.textContent?.trim() || '';
        const date = row.querySelector('.xW, .date, time')?.textContent?.trim() || '';
        if (sender || subject) emails.push({from: sender, subject, snippet, date});
    });
    if (!emails.length) {
        document.querySelectorAll('[role="listitem"], [data-convid]').forEach((item, i) => {
            if (i >= 15) return;
            const sender = item.querySelector('[class*="sender"], [class*="from"]')?.textContent?.trim() || '';
            const subject = item.querySelector('[class*="subject"]')?.textContent?.trim() || '';
            const snippet = item.querySelector('[class*="preview"]')?.textContent?.trim() || '';
            if (sender || subject) emails.push({from: sender, subject, snippet, date: ''});
        });
    }
    const text = emails.length
        ? emails.map((e, i) =>
            (i+1) + '. From: ' + e.from + '\\n   Subject: ' + e.subject +
            (e.snippet ? '\\n   ' + e.snippet : '') +
            (e.date ? '\\n   Date: ' + e.date : '')
        ).join('\\n\\n')
        : 'No emails found. The page might need login or has a different layout.';
    return { title: document.title, text, url: location.href, type: 'email', email_count: emails.length };
}
"""

JS_WHATSAPP = """
() => {
    const chats = [];
    document.querySelectorAll('[data-testid="cell-frame-container"]').forEach((chat, i) => {
        if (i >= 15) return;
        const name = chat.querySelector('span[title]')?.getAttribute('title') || '';
        const lastMsg = chat.querySelector('[data-testid="last-msg-status"]')?.parentElement?.textContent?.trim() || '';
        const time = chat.querySelector('div[data-testid="cell-frame-secondary"] span')?.textContent?.trim() || '';
        const unread = chat.querySelector('[data-testid="icon-unread-count"]')?.textContent?.trim() || '0';
        if (name) chats.push({name, lastMsg, time, unread: parseInt(unread) || 0});
    });
    const text = chats.length
        ? chats.map((c, i) =>
            (i+1) + '. ' + c.name + (c.unread > 0 ? ' [' + c.unread + ' new]' : '') +
            '\\n   Last: ' + c.lastMsg + (c.time ? '  (' + c.time + ')' : '')
        ).join('\\n\\n')
        : 'No chats found. WhatsApp may need login (scan QR code).';
    return {
        title: 'WhatsApp Web', text, url: location.href, type: 'whatsapp',
        chat_count: chats.length, unread_count: chats.filter(c => c.unread > 0).length
    };
}
"""

# ── Extractors map ───────────────────────────────────────────────────────

EXTRACTORS = {
    "search": JS_SEARCH,
    "email": JS_EMAIL,
    "article": JS_ARTICLE,
    "whatsapp": JS_WHATSAPP,
    "generic": JS_GENERIC,
}


def _detect_page_type(url: str) -> str:
    """Auto-detect page type from URL."""
    host = urlparse(url).hostname or ""
    path = urlparse(url).path or ""

    if any(s in host for s in ("google.", "bing.", "duckduckgo.", "search.")):
        if "/search" in path or "q=" in url:
            return "search"
    if any(s in host for s in ("gmail.", "mail.google.", "outlook.", "mail.")):
        return "email"
    if "web.whatsapp.com" in host:
        return "whatsapp"
    return "auto"


class PageReader:
    """Lightweight page reader: Playwright + JS extractors + optional LLM analysis."""

    def __init__(
        self,
        config: Any | None = None,
        session_pool: Any | None = None,
        llm_router: Any | None = None,
    ) -> None:
        """
        Args:
            config: Config for profile directory resolution.
            session_pool: BrowserSessionPool for session reuse.
            llm_router: LLMRouter for LLM-powered analysis.
        """
        self._config = config
        self._session_pool = session_pool
        self._llm_router = llm_router
        self._pw: Any | None = None
        self._browser: Any | None = None
        self._browser_context: Any | None = None

    async def _get_page(self, user_id: str | None = None) -> tuple[Any, bool]:
        """Get a Playwright page.

        Priority:
        1. Reuse live agent session (same cookies, same browser)
        2. Launch standalone browser WITH user's profile dir (persistent cookies)
        3. Launch anonymous standalone browser (no cookies)
        """
        # 1. Try reusing active agent session
        if self._session_pool and user_id:
            try:
                session = await self._session_pool.get_session(user_id)
                if await session.is_alive():
                    browser = session._browser
                    bc = await browser._get_browser_context()
                    if bc:
                        page = await bc.new_page()
                        session.touch()
                        logger.info("PageReader: reusing agent session for user %s", user_id)
                        return page, True
            except Exception as exc:
                logger.debug("PageReader: couldn't reuse agent session: %s", exc)

        # 2. Launch system Chrome with persistent profile (shared with CDP + SmartBrowser)
        if not self._pw:
            from pathlib import Path

            from playwright.async_api import async_playwright

            self._pw = await async_playwright().start()

            if self._config and user_id:
                profile_dir = Path(self._config.database_dir) / "browser_profiles" / user_id
                profile_dir.mkdir(parents=True, exist_ok=True)
                # Use detected browser (Brave > Chrome) with persistent profile
                # Shares cookies + IndexedDB + localStorage with CDP and SmartBrowser
                launch_kwargs = {
                    "headless": True,
                    "args": ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
                    "ignore_https_errors": True,
                }
                if self._config.browser_executable:
                    launch_kwargs["executable_path"] = self._config.browser_executable
                else:
                    launch_kwargs["channel"] = "chrome"

                self._browser_context = await self._pw.chromium.launch_persistent_context(
                    str(profile_dir), **launch_kwargs,
                )
                self._browser = None  # persistent context manages its own browser
                logger.info("PageReader: launched with shared Chrome profile for user %s", user_id)
            else:
                self._browser = await self._pw.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
                )
                logger.info("PageReader: launched anonymous standalone browser")

        if self._browser_context:
            page = await self._browser_context.new_page()
        else:
            page = await self._browser.new_page()
        return page, False

    async def _try_login(self, page: Any, credentials: dict) -> None:
        """Detect login form and fill credentials."""
        try:
            password_field = await page.query_selector('input[type="password"]')
            if not password_field:
                return

            username_field = await page.query_selector(
                'input[type="email"], input[type="text"][name*="user"], '
                'input[type="text"][name*="login"], input[type="text"][name*="email"], '
                'input[type="text"][autocomplete*="user"]'
            )
            if not username_field:
                username_field = await page.query_selector('input[type="text"]')
            if not username_field:
                return

            await username_field.fill(credentials["username"])
            await password_field.fill(credentials["password"])

            submit = await page.query_selector(
                'button[type="submit"], input[type="submit"], '
                'button:has-text("Log in"), button:has-text("Sign in"), '
                'button:has-text("Login")'
            )
            if submit:
                await submit.click()
            else:
                await password_field.press("Enter")

            await page.wait_for_load_state("domcontentloaded", timeout=10000)
            await page.wait_for_timeout(1500)
            logger.info("PageReader: auto-login completed")
        except Exception as exc:
            logger.warning("PageReader: auto-login failed: %s", exc)

    async def _resolve_credentials(self, user_id: str | None, domain: str) -> dict | None:
        """Try to load saved credentials for a domain from the vault.

        Vault key format: `site:{domain}` with JSON value {"username": ..., "password": ...}
        """
        if not self._config or not user_id:
            return None
        try:
            from lazyclaw.crypto.vault import get_credential

            raw = await get_credential(self._config, user_id, f"site:{domain}")
            if raw:
                import json

                creds = json.loads(raw)
                if creds.get("username") and creds.get("password"):
                    logger.info("PageReader: loaded vault credentials for %s", domain)
                    return creds
        except Exception as exc:
            logger.debug("PageReader: no vault credentials for %s: %s", domain, exc)
        return None

    async def _detect_login_page(self, page: Any) -> bool:
        """Check if current page has a login form."""
        try:
            return await page.evaluate(
                "() => !!(document.querySelector('input[type=\"password\"]'))"
            )
        except Exception:
            return False

    async def read_page(
        self,
        url: str,
        user_id: str | None = None,
        custom_extractor: str | None = None,
        credentials: dict | None = None,
    ) -> dict:
        """Load URL and extract clean content via JS extractor.

        Auto-login flow:
        1. Load page with saved cookies
        2. If login form detected → check vault for site:{domain} credentials
        3. Auto-login → save fresh cookies
        4. Extract content

        Returns: {"title": str, "text": str, "type": str, "url": str, ...}
        """
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        page, is_reused = await self._get_page(user_id)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(1500)

            # Auto-login: use provided credentials, or detect login page and pull from vault
            if credentials and not is_reused:
                await self._try_login(page, credentials)
                await self._save_cookies(user_id)
            elif not is_reused and await self._detect_login_page(page):
                domain = urlparse(url).hostname or ""
                vault_creds = await self._resolve_credentials(user_id, domain)
                if vault_creds:
                    await self._try_login(page, vault_creds)
                    await self._save_cookies(user_id)

            if custom_extractor:
                js = custom_extractor
            else:
                page_type = _detect_page_type(url)
                if page_type == "auto":
                    has_article = await page.evaluate("() => !!document.querySelector('article')")
                    page_type = "article" if has_article else "generic"
                js = EXTRACTORS.get(page_type, JS_GENERIC)

            result = await page.evaluate(js)

            if not result or not result.get("text"):
                text = await page.evaluate(
                    "() => document.body?.innerText?.substring(0, 6000) || ''"
                )
                result = {
                    "title": await page.title(),
                    "text": text,
                    "url": url,
                    "type": "fallback",
                }

            logger.info(
                "PageReader: extracted %d chars from %s (type=%s)",
                len(result.get("text", "")),
                url,
                result.get("type"),
            )
            return result
        except Exception as exc:
            logger.error("PageReader: failed to read %s: %s", url, exc)
            return {"title": "", "text": f"Error loading page: {exc}", "url": url, "type": "error"}
        finally:
            try:
                await page.close()
            except Exception:
                pass

    async def read_and_analyze(
        self,
        url: str,
        question: str,
        user_id: str | None = None,
        custom_extractor: str | None = None,
    ) -> str:
        """Read page + analyze with LLM."""
        from lazyclaw.llm.providers.base import LLMMessage

        data = await self.read_page(url, user_id, custom_extractor=custom_extractor)
        content = data.get("text", "")
        title = data.get("title", "")
        page_type = data.get("type", "generic")

        if not content or content.startswith("Error"):
            return content or "Could not load the page."

        if not self._llm_router:
            header = f"**{title}** ({page_type})\n\n" if title else ""
            return header + content

        system_msg = (
            "You are a web content analyst. The user will give you extracted text from a web page "
            "and ask a question about it. Analyze the content and answer concisely. "
            "If the content doesn't contain the answer, say so."
        )
        user_msg = (
            f"Page: {title} ({url})\nType: {page_type}\n\n"
            f"Question: {question}\n\nPage content:\n{content}"
        )

        try:
            response = await self._llm_router.chat(
                messages=[
                    LLMMessage(role="system", content=system_msg),
                    LLMMessage(role="user", content=user_msg),
                ],
                user_id=user_id,
            )
            return response.content or content
        except Exception as exc:
            logger.error("PageReader: LLM analysis failed: %s", exc)
            header = f"**{title}**\n\n" if title else ""
            return header + content

    async def get_dom_structure(
        self,
        url: str,
        user_id: str | None = None,
        credentials: dict | None = None,
    ) -> str:
        """Get simplified DOM structure for LLM extractor generation."""
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        page, is_reused = await self._get_page(user_id)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(1500)
            if credentials and not is_reused:
                await self._try_login(page, credentials)

            structure = await page.evaluate("""() => {
                function walk(node, depth) {
                    if (depth > 6 || !node) return '';
                    if (node.nodeType !== 1) return '';
                    const tag = node.tagName.toLowerCase();
                    if (['script','style','noscript','svg','path'].includes(tag)) return '';
                    let line = '  '.repeat(depth) + '<' + tag;
                    if (node.id) line += ' id="' + node.id + '"';
                    if (node.className && typeof node.className === 'string') {
                        const cls = node.className.trim().substring(0, 80);
                        if (cls) line += ' class="' + cls + '"';
                    }
                    line += '>';
                    let result = line + '\\n';
                    for (const child of node.children) result += walk(child, depth + 1);
                    return result;
                }
                const main = document.querySelector(
                    'main, article, [role="main"], .content, #content'
                ) || document.body;
                let dom = walk(main, 0);
                if (dom.length > 4000) dom = dom.substring(0, 4000) + '\\n... [truncated]';
                return dom;
            }""")
            return structure or ""
        except Exception as exc:
            logger.error("PageReader: get_dom_structure failed for %s: %s", url, exc)
            return ""
        finally:
            try:
                await page.close()
            except Exception:
                pass

    async def generate_extractor(
        self,
        url: str,
        description: str,
        user_id: str | None = None,
        credentials: dict | None = None,
    ) -> dict | None:
        """Auto-generate a JS extractor for a URL via LLM.

        Returns {"js_code": str, "preview": dict} on success, None on failure.
        """
        from lazyclaw.llm.providers.base import LLMMessage

        if not self._llm_router:
            return None

        try:
            raw_data = await self.read_page(url, user_id, credentials=credentials)
            dom = await self.get_dom_structure(url, user_id, credentials=credentials)
            raw_text = raw_data.get("text", "")[:2000]

            if not dom and not raw_text:
                return None

            prompt = (
                "Generate a JavaScript extractor function for a web page.\n"
                "The function MUST be an IIFE: () => { ... } that returns an object with:\n"
                "  { title: string, text: string, url: location.href, type: 'custom',\n"
                "    links: string, images: number, meta: string }\n"
                "Truncate text to 6000 chars max.\n\n"
                f"User wants to extract: {description}\n\n"
                f"Page DOM structure:\n{dom[:3000]}\n\n"
                f"Current raw text:\n{raw_text}\n\n"
                "Return ONLY the JavaScript code. No markdown fences."
            )

            response = await self._llm_router.chat(
                messages=[LLMMessage(role="user", content=prompt)],
                user_id=user_id,
            )
            js_code = (response.content or "").strip()
            if not js_code:
                return None

            # Clean markdown fences if LLM added them
            if js_code.startswith("```"):
                lines = [ln for ln in js_code.split("\n") if not ln.strip().startswith("```")]
                js_code = "\n".join(lines).strip()

            # Test the generated extractor
            preview = await self.read_page(url, user_id, custom_extractor=js_code, credentials=credentials)
            preview_text = preview.get("text", "")
            if not preview_text or len(preview_text) < 50 or preview.get("type") == "error":
                return None

            return {"js_code": js_code, "preview": preview}
        except Exception as exc:
            logger.warning("PageReader: extractor generation failed for %s: %s", url, exc)
            return None

    async def _save_cookies(self, user_id: str | None) -> None:
        """Persist browser cookies to user's profile directory."""
        if not self._browser_context or not self._config or not user_id:
            return
        try:
            import json
            from pathlib import Path

            cookies = await self._browser_context.cookies()
            if cookies:
                profile_dir = Path(self._config.database_dir) / "browser_profiles" / user_id
                profile_dir.mkdir(parents=True, exist_ok=True)
                cookie_file = profile_dir / "cookies.json"
                cookie_file.write_text(json.dumps(cookies))
                logger.info("PageReader: saved %d cookies for user %s", len(cookies), user_id)
        except Exception as exc:
            logger.debug("PageReader: failed to save cookies: %s", exc)

    async def close(self, user_id: str | None = None) -> None:
        """Save cookies and cleanup standalone browser."""
        await self._save_cookies(user_id)
        if self._browser_context:
            try:
                await self._browser_context.close()
            except Exception:
                pass
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
        if self._pw:
            try:
                await self._pw.stop()
            except Exception:
                pass
        self._browser_context = None
        self._browser = None
        self._pw = None
