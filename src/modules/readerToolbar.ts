/** Shared HTML toolbar styling, using Zotero's reader theme and focus tokens. */
export function styleReaderToolbar(doc: Document) {
  if (doc.getElementById("codex-toolbar-style")) return;
  const style = doc.createElementNS("http://www.w3.org/1999/xhtml", "style");
  style.id = "codex-toolbar-style";
  style.textContent = `
    .codex-toolbar-group { display:flex;align-items:center;gap:8px;padding:2px 3px;flex:0 0 auto; }
    .toolbar-button.codex-toolbar-button {
      display:inline-flex;align-items:center;justify-content:center;box-sizing:border-box;
      width:auto;height:28px;min-width:28px;flex:0 0 auto;padding:0 6px;
      border:0;border-radius:5px;font:inherit;white-space:nowrap;
      color:var(--fill-secondary,CanvasText);background:transparent;cursor:default;
    }
    .codex-toolbar-button:hover:not(:disabled) { background:var(--fill-quinary,rgba(127,127,127,.12)); }
    .codex-toolbar-button:active:not(:disabled),.codex-toolbar-button.active-pseudo-class-fix,
    .codex-toolbar-button[aria-pressed="true"] { background:var(--fill-quarternary,rgba(127,127,127,.22)); }
    .codex-toolbar-button[aria-pressed="true"],.codex-toolbar-button:focus-visible {
      outline:none;box-shadow:0 0 0 var(--width-focus-border,2px) var(--color-focus-search,rgba(10,108,245,.5));
    }
    .codex-toolbar-button:disabled { opacity:.5; }
  `;
  (doc.head || doc.documentElement)?.appendChild(style);
}

export function toolbarButton(doc: Document, text: string, extraClass = "") {
  const button = doc.createElementNS("http://www.w3.org/1999/xhtml", "button") as HTMLButtonElement;
  button.type = "button";
  button.className = `toolbar-button codex-toolbar-button ${extraClass}`.trim();
  button.textContent = text;
  button.tabIndex = -1; // Official reader toolbar uses roving keyboard focus.
  // Match the existing reader controls: mouse activation keeps PDF/selection
  // focus, while Enter/Space still use the button's standard click activation.
  button.addEventListener("mousedown", event => {
    if ((event as MouseEvent).button === 0) event.preventDefault();
  });
  return button;
}

/** Recover only a native click retargeted by Gecko after CustomSections detaches
 * a pressed button. Commands still run through click, never pointerdown/up.
 */
export function preserveToolbarClick(doc: Document, group: HTMLElement) {
  let press: { button: HTMLButtonElement; x: number; y: number; moved: boolean; released: boolean; releasedAt?: number } | undefined;
  const down = (event: PointerEvent) => {
    const button = (event.target as Element)?.closest?.("button") as HTMLButtonElement | null;
    press = event.isTrusted && event.button === 0 && button && group.contains(button) && !button.disabled
      ? { button, x: event.clientX, y: event.clientY, moved: false, released: false } : undefined;
  };
  const move = (event: PointerEvent) => {
    if (press && Math.hypot(event.clientX - press.x, event.clientY - press.y) > 6) press.moved = true;
  };
  const up = (event: PointerEvent) => {
    if (press) {
      press.released = event.button === 0 && !press.moved && (event.target as Element)?.closest?.("button") === press.button;
      press.releasedAt = event.timeStamp;
    }
  };
  const cancel = () => { press = undefined; };
  const click = (event: MouseEvent) => {
    if (!event.isTrusted) return;
    const saved = press; press = undefined;
    if (!saved?.released || event.detail < 1 || event.timeStamp - saved.releasedAt! > 500 || saved.button.disabled || !saved.button.isConnected) return;
    if ((event.target as Element)?.closest?.("button") === saved.button) return; // Normal native click.
    // Gecko retargets a click to the common ancestor when the pressed node was
    // removed/reinserted, even if it is the exact same DOM button. Both endpoints
    // must identify our button; no fallback for drags, cancellation or outside-up.
    event.preventDefault();
    event.stopPropagation();
    saved.button.click();
  };
  const listeners: [string, EventListener][] = [
    ["pointerdown", down as EventListener], ["pointermove", move as EventListener],
    ["pointerup", up as EventListener], ["pointercancel", cancel], ["click", click as EventListener],
  ];
  for (const [type, listener] of listeners) doc.addEventListener(type, listener, true);
  return () => { cancel(); for (const [type, listener] of listeners) doc.removeEventListener(type, listener, true); };
}

/** One retained subtree per reader document, including across CustomSections rebuilds. */
export class ReaderToolbarRegistry<T> {
  private entries = new Map<Document, { value: T; release: () => void }>();
  get(doc: Document, create: () => T, dispose: (value: T) => void): T {
    // Gecko can expose distinct compartment wrappers for the same document.
    // DOM identity, rather than wrapper identity, must survive a render callback.
    for (const [known, entry] of this.entries) {
      try { if (known === doc || known.isSameNode?.(doc)) return entry.value; } catch { /* unloaded wrapper */ }
    }
    const value = create();
    const release = () => {
      if (!this.entries.delete(doc)) return;
      try { doc.defaultView?.removeEventListener("unload", release); } catch { /* disposed Gecko window */ }
      dispose(value);
    };
    this.entries.set(doc, { value, release });
    doc.defaultView?.addEventListener("unload", release, { once: true });
    return value;
  }
  clear() {
    for (const { release } of [...this.entries.values()]) release();
  }
}

/** A busy transition belongs to a tab, rather than to a replaceable DOM button. */
export class ComparisonOperations {
  private pending = new Map<string, { kind: "opening" | "closing"; promise: Promise<void> }>();
  readonly listeners = new Set<() => void>();
  status(tabID: string) { return this.pending.get(tabID)?.kind; }
  changed() { for (const listener of this.listeners) listener(); }
  run(tabID: string, kind: "opening" | "closing", action: () => Promise<void>): Promise<void> {
    const existing = this.pending.get(tabID);
    if (existing) return existing.promise;
    // Establish the guard before action can emit render/progress events.
    const entry = { kind, promise: Promise.resolve() };
    entry.promise = Promise.resolve().then(action).finally(() => {
      if (this.pending.get(tabID) === entry) this.pending.delete(tabID);
      this.changed();
    });
    this.pending.set(tabID, entry);
    this.changed();
    return entry.promise;
  }
}
