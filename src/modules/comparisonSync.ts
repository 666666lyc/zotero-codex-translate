/** Position-only synchronization. PDF.js performs page rendering and rotation. */
export type Side = "left" | "right";
export type PageAnchor = { page: number; x: number; y: number };
const clamp = (v: number) => Math.max(0, Math.min(1, v));

export function readPageAnchor(viewer: any): PageAnchor | undefined {
  const container = viewer?.container;
  if (!container) return;
  const rect = container.getBoundingClientRect();
  const x = rect.left + container.clientWidth * .5;
  const y = rect.top + container.clientHeight * .35;
  let chosen: any, distance = Infinity;
  for (let i = 0; i < viewer.pagesCount; i++) {
    const page = viewer.getPageView(i), bounds = page?.div?.getBoundingClientRect();
    if (!bounds?.width || !bounds.height) continue;
    const d = Math.max(bounds.top - y, y - bounds.bottom, 0);
    if (d < distance) { chosen = { page, bounds, index: i }; distance = d; }
    if (!d) break;
  }
  if (!chosen) return;
  const { page, bounds, index } = chosen;
  const [px, py] = page.viewport.convertToPdfPoint(
    (x - bounds.left) * page.viewport.width / bounds.width,
    (y - bounds.top) * page.viewport.height / bounds.height);
  const [x0, y0, x1, y1] = page.viewport.viewBox;
  return { page: index, x: clamp((px - x0) / (x1 - x0)), y: clamp((py - y0) / (y1 - y0)) };
}

export function applyPageAnchor(viewer: any, anchor: PageAnchor) {
  const container = viewer?.container;
  if (!container || anchor.page >= viewer.pagesCount) return;
  if (viewer.currentPageNumber !== anchor.page + 1) viewer.currentPageNumber = anchor.page + 1;
  const page = viewer.getPageView(anchor.page);
  if (!page?.viewport || !page.div) return;
  const [x0, y0, x1, y1] = page.viewport.viewBox;
  const [x, y] = page.viewport.convertToViewportPoint(x0 + anchor.x * (x1-x0), y0 + anchor.y * (y1-y0));
  const rect = container.getBoundingClientRect(), bounds = page.div.getBoundingClientRect();
  const top = container.scrollTop + bounds.top - rect.top + y * bounds.height / page.viewport.height - container.clientHeight * .35;
  const left = container.scrollLeft + bounds.left - rect.left + x * bounds.width / page.viewport.width - container.clientWidth * .5;
  // A synchronous position write leaves no stale async navigation to overwrite
  // a later user gesture. Generated target scroll events never acquire control.
  if (Math.abs(top-container.scrollTop) > 1 || Math.abs(left-container.scrollLeft) > 1)
    container.scrollTo(Math.max(0, left), Math.max(0, top));
}

export class ComparisonSync {
  private source: Side;
  private frame?: number;
  private disposed = false;
  private cleanup: (() => void)[] = [];
  constructor(private options: {
    window: Window; initial: Side; viewer: (side: Side) => any;
    documents: (side: Side) => Document[]; enabled: () => boolean;
    active: (side: Side) => void;
  }) {
    this.source = options.initial;
    for (const side of ["left", "right"] as Side[]) {
      const listen = (target: EventTarget | undefined, type: string, callback: EventListener) => {
        if (!target) return;
        target.addEventListener(type, callback, {capture: true, passive: true});
        this.cleanup.push(() => target.removeEventListener(type, callback, true));
      };
      const acquire = () => { this.source = side; options.active(side); };
      for (const doc of options.documents(side)) {
        for (const event of ["wheel", "pointerdown", "mousedown", "keydown", "touchstart"])
          listen(doc, event, acquire);
      }
      listen(options.viewer(side)?.container, "scroll", () => this.changed(side));
    }
    this.changed(this.source);
  }
  changed(side: Side) {
    if (this.disposed || side !== this.source || !this.options.enabled() || this.frame !== undefined) return;
    this.frame = this.options.window.requestAnimationFrame(() => {
      this.frame = undefined;
      if (this.disposed || !this.options.enabled()) return;
      try {
        const anchor = readPageAnchor(this.options.viewer(this.source));
        if (anchor) applyPageAnchor(this.options.viewer(this.source === "left" ? "right" : "left"), anchor);
      } catch { /* A replaced browser is disconnected by its split-view owner. */ }
    });
  }
  dispose() {
    this.disposed = true;
    if (this.frame !== undefined) this.options.window.cancelAnimationFrame(this.frame);
    for (const remove of this.cleanup) { try { remove(); } catch { /* Dead Gecko compartment. */ } }
    this.cleanup = [];
  }
}
