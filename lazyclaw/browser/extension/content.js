/**
 * LazyClaw Ref Engine — assigns ref IDs to interactive elements.
 *
 * Exposes window.__lazyclaw with snapshot/resolve/click/focus methods.
 * Called from Python via CDP Runtime.evaluate("window.__lazyclaw.snapshot()").
 */
(() => {
  "use strict";

  // ── State ──────────────────────────────────────────────────────────
  let _version = 0;
  let _dirty = false;
  const _refs = new Map();          // refId → Element
  const _refMeta = new Map();       // refId → {role, name, tag, landmark, props}

  // ── Constants ──────────────────────────────────────────────────────
  // Interactive elements + list items (emails, messages, feed items)
  const INTERACTIVE_SELECTOR =
    'input, button, select, textarea, a[href], ' +
    '[role="button"], [role="link"], [role="tab"], [role="menuitem"], ' +
    '[role="checkbox"], [role="radio"], [role="switch"], [role="option"], ' +
    '[role="combobox"], [role="searchbox"], [role="slider"], ' +
    '[role="row"], [role="listitem"], [role="treeitem"], [role="gridcell"], ' +
    '[onclick], [jsaction], [data-action], ' +
    '[role="alert"], [role="status"], ' +
    '[contenteditable="true"], [tabindex]:not([tabindex="-1"]), ' +
    'summary, details';

  // Limit how many list items (rows) we capture to avoid token bloat
  const MAX_ROW_ITEMS = 5;

  const LANDMARK_ROLES = new Set([
    "navigation", "main", "complementary", "banner", "contentinfo",
    "search", "form", "region",
  ]);
  const LANDMARK_TAGS = {
    NAV: "navigation", MAIN: "main", ASIDE: "complementary",
    HEADER: "banner", FOOTER: "contentinfo", FORM: "form",
    SECTION: "region",
  };

  const SKIP_ROLES = new Set([
    "none", "presentation", "generic",
  ]);

  // ── Helpers ────────────────────────────────────────────────────────

  function _isVisible(el) {
    if (el.offsetWidth === 0 && el.offsetHeight === 0) return false;
    const style = getComputedStyle(el);
    return style.display !== "none"
      && style.visibility !== "hidden"
      && style.opacity !== "0";
  }

  function _getLandmark(el) {
    let node = el;
    while (node && node !== document.body) {
      const role = node.getAttribute("role");
      if (role && LANDMARK_ROLES.has(role)) return role;
      const tagLandmark = LANDMARK_TAGS[node.tagName];
      if (tagLandmark) return tagLandmark;
      node = node.parentElement;
    }
    return "other";
  }

  function _getRole(el) {
    const explicit = el.getAttribute("role");
    if (explicit && !SKIP_ROLES.has(explicit)) return explicit;
    const tag = el.tagName.toLowerCase();
    const type = el.getAttribute("type");
    if (tag === "input") return type === "checkbox" ? "checkbox"
      : type === "radio" ? "radio"
      : type === "submit" || type === "button" ? "button"
      : "textbox";
    if (tag === "button") return "button";
    if (tag === "a") return "link";
    if (tag === "select") return "combobox";
    if (tag === "textarea") return "textbox";
    if (tag === "summary") return "button";
    return tag;
  }

  function _getName(el) {
    return (
      el.getAttribute("aria-label")
      || el.getAttribute("data-tooltip")
      || el.getAttribute("title")
      || el.getAttribute("alt")
      || el.getAttribute("placeholder")
      || (el.tagName === "INPUT" || el.tagName === "TEXTAREA"
        ? (el.labels?.[0]?.textContent || "").trim()
        : "")
      || (el.textContent || "").trim().slice(0, 80)
      || ""
    );
  }

  function _getProps(el) {
    const props = {};
    if (el.checked) props.checked = "true";
    if (el.selected || el.getAttribute("aria-selected") === "true") props.selected = "true";
    if (el.disabled) props.disabled = "true";
    if (el.getAttribute("aria-expanded")) props.expanded = el.getAttribute("aria-expanded");
    if (el.getAttribute("aria-pressed")) props.pressed = el.getAttribute("aria-pressed");
    if (el.required) props.required = "true";
    if (el.placeholder) props.placeholder = el.placeholder;
    if (el.type && el.type !== "text") props.type = el.type;
    if (el.value && el.type !== "password" && el.value.length < 60) props.value = el.value;
    if (el.href) props.href = el.href.slice(0, 120);
    return props;
  }

  function _walkElements(root, prefix) {
    const elements = [];
    try {
      const candidates = root.querySelectorAll(INTERACTIVE_SELECTOR);
      for (const el of candidates) {
        if (!_isVisible(el)) continue;
        elements.push({ el, prefix });
      }
    } catch (_) { /* CSP or security error */ }

    // Same-origin iframes
    try {
      const iframes = root.querySelectorAll("iframe");
      let frameIdx = 0;
      for (const iframe of iframes) {
        frameIdx++;
        try {
          const doc = iframe.contentDocument;
          if (doc) {
            const fp = prefix ? `${prefix}f${frameIdx}_` : `f${frameIdx}_`;
            const frameEls = _walkElements(doc, fp);
            elements.push(...frameEls);
          }
        } catch (_) {
          // Cross-origin — record as opaque
          elements.push({
            el: iframe,
            prefix,
            crossOrigin: true,
            src: iframe.src || "",
          });
        }
      }
    } catch (_) { /* ignore */ }

    return elements;
  }

  // ── MutationObserver ───────────────────────────────────────────────
  const _observer = new MutationObserver(() => { _dirty = true; });
  _observer.observe(document.body || document.documentElement, {
    childList: true, subtree: true, attributes: true,
    attributeFilter: ["disabled", "aria-hidden", "hidden", "style", "class"],
  });

  // ── Public API ─────────────────────────────────────────────────────

  const api = {
    snapshot() {
      _refs.clear();
      _refMeta.clear();
      _version++;
      _dirty = false;

      const rawElements = _walkElements(document, "");
      const landmarks = {};   // landmark → [{refId, ...}]
      const elements = {};    // refId → meta
      let counter = 0;

      // Track row-type items per landmark to limit token usage
      const rowCounts = {};   // landmark → total row count
      const rowShown = {};    // landmark → shown count

      for (const item of rawElements) {
        if (item.crossOrigin) {
          counter++;
          const refId = `${item.prefix || ""}e${counter}`;
          const meta = {
            role: "iframe",
            name: `cross-origin: ${item.src.slice(0, 80)}`,
            tag: "iframe",
            landmark: _getLandmark(item.el),
            props: {},
          };
          _refs.set(refId, item.el);
          _refMeta.set(refId, meta);
          elements[refId] = meta;
          const lm = meta.landmark;
          if (!landmarks[lm]) landmarks[lm] = [];
          landmarks[lm].push(refId);
          continue;
        }

        const el = item.el;
        const role = _getRole(el);
        const tag = el.tagName.toLowerCase();
        const landmark = _getLandmark(el);

        // Count all rows but only keep first MAX_ROW_ITEMS per landmark
        const isRowLike = (role === "row" || role === "listitem" ||
          role === "treeitem" || role === "gridcell");
        if (isRowLike) {
          rowCounts[landmark] = (rowCounts[landmark] || 0) + 1;
          rowShown[landmark] = rowShown[landmark] || 0;
          if (rowShown[landmark] >= MAX_ROW_ITEMS) {
            // Still store the ref (for clicking) but don't include in elements
            counter++;
            const refId = `${item.prefix || ""}e${counter}`;
            _refs.set(refId, el);
            _refMeta.set(refId, {
              role, name: _getName(el), tag, landmark, props: _getProps(el),
            });
            continue; // skip adding to elements/landmarks output
          }
          rowShown[landmark]++;
        }

        counter++;
        const refId = `${item.prefix || ""}e${counter}`;
        const name = _getName(el);
        const props = _getProps(el);

        const meta = { role, name, tag, landmark, props };
        _refs.set(refId, el);
        _refMeta.set(refId, meta);
        elements[refId] = meta;

        if (!landmarks[landmark]) landmarks[landmark] = [];
        landmarks[landmark].push(refId);
      }

      // Build page context summary
      const context = {};
      for (const [lm, total] of Object.entries(rowCounts)) {
        const shown = rowShown[lm] || 0;
        if (total > shown) {
          context[lm + "_rows"] = `${total} total (showing first ${shown})`;
        }
      }

      // Capture brief main content text so LLM sees "No results" messages etc.
      try {
        const mainEl = document.querySelector('[role="main"], main, #content');
        if (mainEl) {
          const mainText = mainEl.innerText.trim().slice(0, 1000);
          if (mainText) context.main_text = mainText;
        }
      } catch (_) {}

      return {
        version: _version,
        url: location.href,
        title: document.title || "",
        landmarks,
        elements,
        elementCount: counter,
        context,
        dirty: false,
      };
    },

    resolve(refId) {
      const el = _refs.get(refId);
      if (!el || !el.isConnected) return null;
      const rect = el.getBoundingClientRect();
      if (rect.width === 0 && rect.height === 0) return null;
      return {
        x: rect.x + rect.width / 2,
        y: rect.y + rect.height / 2,
        width: rect.width,
        height: rect.height,
        visible: _isVisible(el),
      };
    },

    click(refId) {
      const el = _refs.get(refId);
      if (!el || !el.isConnected) return null;
      el.scrollIntoView({ block: "center", behavior: "instant" });
      const rect = el.getBoundingClientRect();
      if (rect.width === 0 && rect.height === 0) return null;
      return {
        x: rect.x + rect.width / 2,
        y: rect.y + rect.height / 2,
      };
    },

    performClick(refId) {
      const el = _refs.get(refId);
      if (!el || !el.isConnected) return false;
      el.scrollIntoView({ block: "center", behavior: "instant" });
      el.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true }));
      el.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true }));
      el.click();
      return true;
    },

    focus(refId) {
      const el = _refs.get(refId);
      if (!el || !el.isConnected) return false;
      el.scrollIntoView({ block: "center", behavior: "instant" });
      el.focus();
      // Select all existing text so typing replaces it
      if (el.select) el.select();
      else if (el.setSelectionRange && el.value !== undefined) {
        el.setSelectionRange(0, el.value.length);
      }
      return document.activeElement === el;
    },

    getVersion() {
      return _version;
    },

    isDirty() {
      return _dirty;
    },

    getMeta(refId) {
      return _refMeta.get(refId) || null;
    },
  };

  // Expose on window — accessible via CDP Runtime.evaluate
  Object.defineProperty(window, "__lazyclaw", {
    value: Object.freeze(api),
    writable: false,
    configurable: false,
  });
})();
