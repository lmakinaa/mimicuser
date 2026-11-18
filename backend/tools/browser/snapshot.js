/**
 * Page snapshot: stamp stable handles, return a filtered accessibility-ish tree.
 *
 * This is the one primitive all five reference systems converged on — BrowserGym's
 * `bid`, UXAgent's `data-semantic-id`, Harness's Set-of-Mark ids. The agent names a
 * *thing on the page*; it never sees a CSS selector and never sees a pixel
 * coordinate. That is what makes a session replayable and its evidence meaningful.
 *
 * Returns { url, title, tree, refs, truncated }.
 *
 * Handles are minted per snapshot and written to `data-sc-ref` on the live DOM, so
 * a handle from an older snapshot resolves to nothing after a re-render — which is
 * correct: the thing the customer was looking at really is gone.
 */
// Expressed as an arrow function, not an IIFE: Playwright decides whether to
// *call* an evaluated string or merely evaluate it, and an IIFE can land on the
// wrong side of that heuristic and return undefined.
() => {
  const REF_ATTR = 'data-sc-ref';
  const MAX_NODES = 400;
  const MAX_TEXT = 160;

  // LABEL is deliberately absent: its text already surfaces as the accessible
  // name of the control it labels, and emitting it separately produces a
  // duplicate `generic` node the customer would never think of as a thing.
  const INTERACTIVE_TAGS = new Set([
    'A', 'BUTTON', 'INPUT', 'SELECT', 'TEXTAREA', 'SUMMARY', 'OPTION',
  ]);
  const INTERACTIVE_ROLES = new Set([
    'button', 'link', 'checkbox', 'radio', 'switch', 'tab', 'menuitem',
    'menuitemcheckbox', 'menuitemradio', 'option', 'textbox', 'combobox',
    'searchbox', 'slider', 'spinbutton', 'treeitem',
  ]);
  const STRUCTURAL_ROLES = new Set([
    'heading', 'navigation', 'main', 'banner', 'contentinfo', 'form', 'search',
    'dialog', 'alertdialog', 'alert', 'status', 'table', 'list', 'listitem',
    'article', 'region', 'tablist', 'menu',
  ]);
  const SKIP_TAGS = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'SVG', 'PATH', 'HEAD', 'META', 'LINK']);

  let counter = 0;
  const refs = {};

  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim().slice(0, MAX_TEXT);

  function isVisible(el) {
    if (!(el instanceof Element)) return false;
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    if (parseFloat(style.opacity || '1') < 0.05) return false;
    if (el.hasAttribute('hidden') || el.getAttribute('aria-hidden') === 'true') return false;
    const rect = el.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) return false;
    // Off-canvas but still in a scroll container counts as present; fully
    // outside the document does not.
    if (rect.bottom < -2000 || rect.top > (document.documentElement.scrollHeight + 2000)) return false;
    return true;
  }

  function inViewport(el) {
    const r = el.getBoundingClientRect();
    return r.bottom > 0 && r.top < window.innerHeight;
  }

  function roleOf(el) {
    const explicit = el.getAttribute('role');
    if (explicit) return explicit.toLowerCase();
    const tag = el.tagName;
    switch (tag) {
      case 'A': return el.hasAttribute('href') ? 'link' : 'generic';
      case 'BUTTON': return 'button';
      case 'SELECT': return 'combobox';
      case 'TEXTAREA': return 'textbox';
      case 'SUMMARY': return 'button';
      case 'IMG': return 'image';
      case 'NAV': return 'navigation';
      case 'MAIN': return 'main';
      case 'HEADER': return 'banner';
      case 'FOOTER': return 'contentinfo';
      case 'FORM': return 'form';
      case 'TABLE': return 'table';
      case 'UL': case 'OL': return 'list';
      case 'LI': return 'listitem';
      case 'H1': case 'H2': case 'H3': case 'H4': case 'H5': case 'H6': return 'heading';
      case 'INPUT': {
        const t = (el.getAttribute('type') || 'text').toLowerCase();
        if (t === 'checkbox') return 'checkbox';
        if (t === 'radio') return 'radio';
        if (t === 'submit' || t === 'button' || t === 'reset') return 'button';
        if (t === 'search') return 'searchbox';
        if (t === 'hidden') return 'none';
        return 'textbox';
      }
      default: return 'generic';
    }
  }

  /**
   * Accessible name, in roughly the order the AX spec prefers.
   *
   * `allowTextName` is false for containers. `textContent` on a <nav> holding
   * two links yields "PricingDocs" — no separator, and a name no human would
   * recognise. Containers get a name only when the page states one explicitly.
   */
  function nameOf(el, allowTextName = true) {
    const labelledby = el.getAttribute('aria-labelledby');
    if (labelledby) {
      const parts = labelledby.split(/\s+/)
        .map((id) => document.getElementById(id))
        .filter(Boolean)
        .map((n) => n.textContent);
      if (parts.length) return clean(parts.join(' '));
    }
    const aria = el.getAttribute('aria-label');
    if (aria) return clean(aria);

    if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT') {
      if (el.id) {
        const lbl = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
        if (lbl && lbl.textContent.trim()) return clean(lbl.textContent);
      }
      const wrapping = el.closest('label');
      if (wrapping && wrapping.textContent.trim()) return clean(wrapping.textContent);
      const ph = el.getAttribute('placeholder');
      if (ph) return clean(ph);
      const nm = el.getAttribute('name');
      if (nm) return clean(nm);
    }
    if (el.tagName === 'IMG') return clean(el.getAttribute('alt') || '');
    const title = el.getAttribute('title');
    if (title) return clean(title);
    if (!allowTextName) return '';

    // Own text only — do not inherit a whole section's text into a button.
    const own = Array.from(el.childNodes)
      .filter((n) => n.nodeType === Node.TEXT_NODE)
      .map((n) => n.textContent)
      .join(' ');
    if (clean(own)) return clean(own);

    // Small containers may legitimately borrow their descendant text.
    const txt = clean(el.textContent);
    if (txt && txt.length <= 80) return txt;
    return '';
  }

  function valueOf(el) {
    if (el.tagName === 'INPUT') {
      const t = (el.getAttribute('type') || 'text').toLowerCase();
      if (t === 'password') return el.value ? '••••••' : '';
      if (t === 'checkbox' || t === 'radio') return el.checked ? 'checked' : 'unchecked';
      return clean(el.value);
    }
    if (el.tagName === 'TEXTAREA') return clean(el.value);
    if (el.tagName === 'SELECT') {
      const opt = el.options[el.selectedIndex];
      return opt ? clean(opt.textContent) : '';
    }
    return '';
  }

  function isInteractive(el, role) {
    if (INTERACTIVE_TAGS.has(el.tagName)) return true;
    if (INTERACTIVE_ROLES.has(role)) return true;
    if (el.hasAttribute('onclick')) return true;
    if (el.tabIndex >= 0 && el.tagName !== 'BODY') return true;
    if (el.isContentEditable) return true;
    return false;
  }

  function annotate(el, role) {
    const bits = [];
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') bits.push('disabled');
    if (el.getAttribute('aria-expanded')) bits.push(`expanded=${el.getAttribute('aria-expanded')}`);
    if (el.required || el.getAttribute('aria-required') === 'true') bits.push('required');
    if (el.tagName === 'INPUT') {
      const t = (el.getAttribute('type') || 'text').toLowerCase();
      if (t !== 'text') bits.push(`type=${t}`);
    }
    if (el.tagName === 'H1' || el.tagName === 'H2' || el.tagName === 'H3'
        || el.tagName === 'H4' || el.tagName === 'H5' || el.tagName === 'H6') {
      bits.push(`level=${el.tagName[1]}`);
    }
    if (role === 'link' && el.getAttribute('href')) {
      const href = el.getAttribute('href');
      if (href && !href.startsWith('#')) bits.push(`href=${href.slice(0, 80)}`);
    }
    if (!inViewport(el)) bits.push('offscreen');
    return bits;
  }

  const lines = [];
  let truncated = false;

  function walk(node, depth) {
    if (lines.length >= MAX_NODES) { truncated = true; return; }
    if (!(node instanceof Element)) return;
    if (SKIP_TAGS.has(node.tagName)) return;
    if (!isVisible(node)) return;

    const role = roleOf(node);
    if (role === 'none') return;

    const interactive = isInteractive(node, role);
    const structural = STRUCTURAL_ROLES.has(role);
    // Headings and list items read as content, not containers, so their own
    // text is the right name; the rest must name themselves explicitly.
    const namesItself = role === 'heading' || role === 'listitem';
    const name = nameOf(node, !structural || namesItself);

    let emittedDepth = depth;
    if (interactive || (structural && name)) {
      const ref = `e${++counter}`;
      node.setAttribute(REF_ATTR, ref);
      const value = valueOf(node);
      const bits = annotate(node, role);

      let line = `${'  '.repeat(Math.min(depth, 8))}[${ref}] ${role}`;
      if (name) line += ` "${name}"`;
      if (value) line += ` value="${value}"`;
      if (bits.length) line += ` (${bits.join(', ')})`;
      lines.push(line);

      refs[ref] = { role, name, tag: node.tagName.toLowerCase() };
      emittedDepth = depth + 1;
    } else if (!interactive && !structural) {
      // Leaf text that is not attached to anything interactive still tells the
      // customer what the page says.
      const hasElementChildren = node.children.length > 0;
      if (!hasElementChildren) {
        const txt = clean(node.textContent);
        if (txt.length > 1) {
          lines.push(`${'  '.repeat(Math.min(depth, 8))}text "${txt}"`);
        }
        return;
      }
    }

    for (const child of node.children) walk(child, emittedDepth);
  }

  document.querySelectorAll(`[${REF_ATTR}]`).forEach((el) => el.removeAttribute(REF_ATTR));
  walk(document.body, 0);

  return {
    url: window.location.href,
    title: document.title,
    tree: lines.join('\n'),
    refs,
    truncated,
    scroll: {
      y: Math.round(window.scrollY),
      height: Math.round(document.documentElement.scrollHeight),
      viewport: Math.round(window.innerHeight),
    },
  };
}
