"""Page Reader — JS extractors for CDP-based page content extraction.

Pure JavaScript extractors for WhatsApp, Gmail, search results, articles,
and generic pages. Used by BrowserSkill and watcher system via CDP evaluate().
"""

from __future__ import annotations

import asyncio
import logging
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
    document.querySelectorAll('[role="row"]').forEach((row, i) => {
        if (i >= 15) return;
        const spans = row.querySelectorAll('span');
        if (spans.length < 2) return;
        const spanTexts = [];
        spans.forEach(s => {
            const t = s.textContent.trim();
            if (t && t.length < 200 && !t.startsWith('ic-') && !t.startsWith('wds-')
                && !t.startsWith('status-') && !t.startsWith('default-')) {
                spanTexts.push(t);
            }
        });
        if (!spanTexts.length) return;
        const name = spanTexts[0] || '';
        let time = '';
        let unread = 0;
        let lastMsg = '';
        for (const t of spanTexts) {
            if (/unread message/.test(t)) {
                const m = t.match(/(\\d+)/);
                if (m) unread = parseInt(m[1]);
            } else if (/yesterday|today|\\d{1,2}[:\\/]\\d{2}/i.test(t)) {
                if (!time) time = t;
            }
        }
        const msgSpans = spanTexts.filter(t =>
            t !== name && !/unread/.test(t) && t !== time && t.length > 1
        );
        lastMsg = msgSpans.slice(-1)[0] || '';
        if (name) chats.push({name, lastMsg, time, unread});
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


async def run_extractor(backend, url: str | None = None) -> dict:
    """Run the appropriate JS extractor on the current page via CDP.

    Detects page type from URL, picks the right extractor (WhatsApp, Gmail,
    search, article, generic), and evaluates it via backend.evaluate().

    Args:
        backend: CDPBackend instance (must be connected to a tab).
        url: Override URL for type detection. If None, reads from backend.

    Returns:
        {"title": str, "text": str, "url": str, "type": str, ...}
    """
    if url is None:
        url = await backend.current_url()
    title = await backend.title()
    page_type = _detect_page_type(url)

    # WhatsApp sync wait
    if page_type == "whatsapp":
        for _ in range(15):
            count = await backend.evaluate(
                "(() => document.querySelectorAll('[role=\"row\"]').length)()"
            )
            if count and count > 0:
                break
            await asyncio.sleep(2)
        result = await backend.evaluate(f"({JS_WHATSAPP})()")
        if isinstance(result, dict):
            return result
        return {"title": title, "text": str(result), "url": url, "type": "whatsapp"}

    # Email extractor
    if page_type == "email":
        result = await backend.evaluate(f"({JS_EMAIL})()")
        if isinstance(result, dict):
            return result
        return {"title": title, "text": str(result), "url": url, "type": "email"}

    # Search extractor
    if page_type == "search":
        result = await backend.evaluate(f"({JS_SEARCH})()")
        if isinstance(result, dict):
            return result
        return {"title": title, "text": str(result), "url": url, "type": "search"}

    # Auto-detect article vs generic
    if page_type == "auto":
        has_article = await backend.evaluate(
            "(() => !!document.querySelector('article'))()"
        )
        page_type = "article" if has_article else "generic"

    js = EXTRACTORS.get(page_type, JS_GENERIC)
    result = await backend.evaluate(f"({js})()")

    if isinstance(result, dict) and result.get("text"):
        return result

    # Fallback: plain innerText
    text = await backend.evaluate(
        "(() => document.body?.innerText?.substring(0, 5000) || '')()"
    )
    return {"title": title, "text": text or "", "url": url, "type": "fallback"}
