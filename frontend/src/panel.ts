// The Yard editor as a Home Assistant panel. HA registers it with embed_iframe, so the
// editor has a document of its own and main.ts looks its elements up by id in it.
// HA sets ``hass`` on the element whenever anything changes; the API keeps the latest.

import maplibreCss from "maplibre-gl/dist/maplibre-gl.css?inline";
import styleCss from "./style.css?inline";
import { setHass, type Hass } from "./api";
import { LAYOUT } from "./layout";

class YarbiePanel extends HTMLElement {
  #started = false;

  set hass(hass: Hass) {
    setHass(hass);
    if (!this.#started) {
      this.#started = true;
      void this.#start();
    }
  }

  async #start() {
    for (const css of [maplibreCss, styleCss]) {
      const style = document.createElement("style");
      style.textContent = css;
      document.head.append(style);
    }
    this.innerHTML = LAYOUT;
    await import("./main");
  }
}

customElements.define("yarbie-panel", YarbiePanel);
