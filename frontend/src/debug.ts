// Diagnostics, only when the panel is opened with ?debug: errors and what each click on
// the map did go to Home Assistant's system log for someone to read.

import { api } from "./api";

const search = (() => {
  try {
    return window.parent.location.search || location.search;
  } catch {
    return location.search;
  }
})();
export const DEBUG = new URLSearchParams(search).has("debug");

export function debugLog(kind: string, detail: unknown = {}) {
  if (!DEBUG) return;
  void api.log(kind, detail)?.catch(() => {});
}

export function installDebug(container: HTMLElement) {
  if (!DEBUG) return;
  window.addEventListener("error", (e) => debugLog("error", { message: e.message, at: `${e.filename}:${e.lineno}` }));
  window.addEventListener("unhandledrejection", (e) => debugLog("rejection", { reason: String(e.reason) }));
  for (const level of ["error", "warn"] as const) {
    const original = console[level].bind(console);
    console[level] = (...args: unknown[]) => {
      debugLog(`console.${level}`, { args: args.map((a) => String(a).slice(0, 500)) });
      original(...args);
    };
  }
  // Raw input on the map, before any library sees it (capture phase).
  for (const type of ["pointerdown", "pointerup", "click"] as const) {
    container.addEventListener(
      type,
      (e) => {
        const target = e.target as HTMLElement;
        debugLog(`dom.${type}`, {
          x: Math.round((e as MouseEvent).clientX),
          y: Math.round((e as MouseEvent).clientY),
          target: `${target.tagName.toLowerCase()}.${target.className}`.slice(0, 120),
          pointerType: (e as PointerEvent).pointerType,
        });
      },
      true,
    );
  }
  debugLog("debug.start", { userAgent: navigator.userAgent, dpr: devicePixelRatio });
}
