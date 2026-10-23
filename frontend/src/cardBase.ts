// What the yarbie dashboard cards share: the hass object, calls to the integration, the
// write rails as the integration reports them, styles, and registering the element.
// Loaded on every page (HA's extra modules), so it imports nothing at run time.

import type { Run } from "./api";

export interface CardHass {
  callWS<T>(msg: Record<string, unknown>): Promise<T>;
  callService(domain: string, service: string, data?: Record<string, unknown>): Promise<unknown>;
  states: Record<string, { state: string; attributes: Record<string, unknown> } | undefined>;
  user?: { is_admin?: boolean; name?: string };
  connection?: {
    subscribeMessage<T>(cb: (event: T) => void, msg: Record<string, unknown>): Promise<() => Promise<void>>;
  };
}

export interface CardConfig {
  type: string;
  entry_id?: string; // which robot; the first one set up when left out
  entity?: string; // an entity whose changes mean the card's data may have changed
  mower?: string; // the robot's lawn_mower entity: its state says whether a plan runs
  title?: string;
}

/** What yarbie/info says about writes (answered from the session; the robot is not asked). */
export interface WriteState {
  connected: boolean;
  writable: boolean;
  read_only_reason: string | null;
  busy: boolean;
}

export const esc = (s: unknown) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

export class CardError extends Error {
  constructor(
    message: string,
    readonly code?: string,
  ) {
    super(message);
  }
}

export abstract class YarbieCard extends HTMLElement {
  protected config: CardConfig = { type: "" };
  protected hassObj: CardHass | null = null;
  protected writes: WriteState | null = null;
  protected message: { text: string; kind: "ok" | "error" | "" } = { text: "", kind: "" };
  #watched = "";
  #mower = "";

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
  }

  setConfig(config: CardConfig) {
    this.config = { ...config };
    if (this.hassObj) void this.load();
  }

  set hass(hass: CardHass) {
    const first = !this.hassObj;
    this.hassObj = hass;
    const watched = this.config.entity ? JSON.stringify(hass.states[this.config.entity]?.attributes ?? null) : "";
    const mower = this.config.mower ? (hass.states[this.config.mower]?.state ?? "") : "";
    if (first) {
      this.#watched = watched;
      this.#mower = mower;
      void this.load();
      return;
    }
    if (mower !== this.#mower) {
      this.#mower = mower;
      void this.refreshWrites();
    }
    if (watched !== this.#watched) {
      this.#watched = watched;
      if (!this.editing()) void this.load();
    }
  }

  getCardSize() {
    return 4;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6 };
  }

  /** Read what the card shows and draw it. */
  protected abstract load(): Promise<void>;
  /** True while a form is open: background updates must not redraw over it. */
  protected abstract editing(): boolean;
  protected abstract render(): void;

  protected get isAdmin(): boolean {
    return this.hassObj?.user?.is_admin !== false;
  }

  protected async ws<T>(type: string, data: Record<string, unknown> = {}): Promise<T> {
    if (!this.hassObj) throw new CardError("not connected to Home Assistant");
    const entry = this.config.entry_id ? { entry_id: this.config.entry_id } : {};
    try {
      return await this.hassObj.callWS<T>({ type: `yarbie/${type}`, ...entry, ...data });
    } catch (err) {
      const e = err as { code?: string; message?: string };
      throw new CardError(e.message ?? String(err), e.code);
    }
  }

  protected async refreshWrites() {
    try {
      this.writes = await this.ws<WriteState>("info");
    } catch {
      this.writes = null;
    }
    if (!this.editing()) this.render();
    else this.updateRails();
  }

  /** Why writes would be refused now, or null when they would be sent. */
  protected refusal(): string | null {
    if (!this.isAdmin) return null; // they cannot write at all, and each card says so
    const w = this.writes;
    if (!w) return "No answer from yarbie.";
    if (!w.connected) return "Not connected to the robot.";
    if (!w.writable) return `Read-only: ${w.read_only_reason ?? "writes are off"}.`;
    if (w.busy) return "The robot is running or paused in a plan. Stop or finish it first.";
    return null;
  }

  /** A card's own reason to keep a write control off (a send in flight, nothing to pick...),
   * on top of the rails. */
  protected blocked(_control: HTMLElement): boolean {
    return false;
  }

  /** Refresh the rail notice and every write control without redrawing an open form. */
  protected updateRails() {
    const refusal = this.refusal();
    const box = this.shadowRoot!.querySelector<HTMLElement>(".rails");
    if (box) {
      box.textContent = refusal ?? "";
      box.hidden = !refusal;
    }
    this.shadowRoot!.querySelectorAll<HTMLButtonElement | HTMLInputElement>("[data-write]").forEach(
      (control) => (control.disabled = !!refusal || this.blocked(control)),
    );
  }

  protected say(text: string, kind: "ok" | "error" | "" = "") {
    this.message = { text, kind };
    const footer = this.shadowRoot!.querySelector<HTMLElement>(".message");
    if (footer) {
      footer.textContent = text;
      footer.className = `message ${kind}`;
    }
  }

  protected frame(title: string, body: string): string {
    const refusal = this.refusal();
    return `<style>${STYLE}</style>
      <ha-card header="${esc(this.config.title ?? title)}">
        <div class="card-content">
          <p class="rails" ${refusal ? "" : "hidden"}>${esc(refusal ?? "")}</p>
          ${body}
          <p class="message ${this.message.kind}">${esc(this.message.text)}</p>
        </div>
      </ha-card>`;
  }

  /** A path inside Home Assistant, the way its own links go there (no page reload). */
  protected navigate(path: string) {
    history.pushState(null, "", path);
    window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
  }
}

/** A parameter of Home Assistant's address (?area=, ?run=, ?plan=): how links from the
 * Yard editor, entities and notifications select something on the dashboard's cards. */
export function queryParam(name: string): string | null {
  return new URLSearchParams(window.location.search).get(name);
}

/** Set or clear one parameter of the address without leaving the view, so a reload or a
 * shared link shows the same thing. */
export function setQueryParam(name: string, value: string | null) {
  const url = new URL(window.location.href);
  if (value === null) url.searchParams.delete(name);
  else url.searchParams.set(name, value);
  history.replaceState(history.state, "", url);
}

/** The runs card showing a run (or none): the yard card shades its areas. */
export const RUN_EVENT = "yarbie-run-shown";
export interface RunShown {
  entry_id: string | null;
  run: Run | null;
}

export function errorText(err: unknown): string {
  if (err instanceof CardError && err.code === "unauthorized") return "Only administrators can change this.";
  return err instanceof Error ? err.message : String(err);
}

export const STYLE = `
  :host { display: block; }
  .card-content { padding: 0 16px 16px; }
  ul.list { list-style: none; margin: 0; padding: 0; }
  ul.list > li { display: flex; justify-content: space-between; align-items: flex-start; gap: 8px;
    padding: 8px 0; border-bottom: 1px solid var(--divider-color); }
  ul.list > li:last-child { border-bottom: none; }
  .meta, .hint { color: var(--secondary-text-color); font-size: 0.9em; }
  .hint { margin: 6px 0; }
  .rails { margin: 0 0 8px; padding: 8px; border-radius: 6px; font-size: 0.9em;
    background: rgba(var(--rgb-warning-color, 255, 166, 0), 0.15); }
  .message { margin: 8px 0 0; font-size: 0.9em; min-height: 1em; }
  .message.error { color: var(--error-color); }
  .message.ok { color: var(--success-color, var(--primary-color)); }
  .row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 8px; }
  .buttons { display: flex; gap: 6px; flex-shrink: 0; }
  button { font: inherit; font-size: 0.9em; padding: 4px 12px; border-radius: 16px; cursor: pointer;
    border: 1px solid var(--divider-color); background: none; color: var(--primary-color); }
  button.primary { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: transparent; }
  button.danger { color: var(--error-color); }
  button.confirm { background: var(--error-color); color: #fff; border-color: transparent; }
  button.icon { padding: 2px 8px; }
  button:disabled { opacity: 0.45; cursor: default; }
  label.field { display: flex; flex-direction: column; gap: 4px; margin: 8px 0; font-size: 0.9em; }
  label.check { display: flex; gap: 8px; align-items: center; margin: 4px 0; }
  input[type="text"], input[type="time"], input[type="number"], select {
    font: inherit; padding: 6px 8px; border-radius: 4px; border: 1px solid var(--divider-color);
    background: var(--card-background-color, transparent); color: var(--primary-text-color); }
  fieldset { border: 1px solid var(--divider-color); border-radius: 6px; margin: 8px 0; padding: 4px 10px 8px; }
  legend { font-size: 0.9em; color: var(--secondary-text-color); }
  .form { border-top: 1px solid var(--divider-color); margin-top: 8px; padding-top: 4px; }
  .order li { align-items: center; padding: 4px 0; }
  .unconfirmed { color: var(--warning-color, #b58100); font-size: 0.85em; }
  .days { display: flex; flex-wrap: wrap; gap: 4px 12px; }
  a { color: var(--primary-color); cursor: pointer; }
`;

declare global {
  interface Window {
    customCards?: { type: string; name: string; description: string; preview?: boolean }[];
  }
}

/** Register a card. HA loads extra modules before its own code, which then swaps in a
 * polyfilled element registry that forgets what was defined earlier (see dashboard.ts),
 * so define it again until it sticks there. */
export function defineCard(tag: string, card: CustomElementConstructor, name: string, description: string) {
  const define = () => {
    if (!customElements.get(tag)) customElements.define(tag, class extends (card as typeof HTMLElement) {});
  };
  define();
  let checks = 0;
  const keep = window.setInterval(() => {
    define();
    if (++checks >= 60) window.clearInterval(keep);
  }, 500);
  window.customCards ??= [];
  if (!window.customCards.some((c) => c.type === tag)) window.customCards.push({ type: tag, name, description });
}
