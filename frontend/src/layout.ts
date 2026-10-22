// The editor's markup, put into the panel's document before main.ts looks for it.

export const LAYOUT = `
<div id="app">
      <aside id="panel">
        <header>
          <h1>Yard editor</h1>
          <span id="status" class="pill">connecting…</span>
        </header>
        <div id="demo-banner" class="banner" hidden>
          <b>Demo yard</b>: a simulated robot, not yours. To use your robot, add it under
          Settings, Devices &amp; services, yarbie.
        </div>

        <section>
          <label class="field" style="margin:0">Imagery<select id="imagery"></select></label>
        </section>

        <section id="run-section" hidden>
          <h2>Current run</h2>
          <label class="check"><input type="checkbox" id="run-on" checked /> Show what has been mowed</label>
          <div id="run-info" class="hint"></div>
        </section>

        <section>
          <h2>Draw</h2>
          <div class="tools" id="tools">
            <button data-tool="select" class="active" title="Select, move vertices (S)">Select</button>
            <button data-tool="nogozones" title="Draw a no-go zone (N)"><i class="sw nogozones"></i>No-go</button>
            <button data-tool="pathways" title="Draw a pathway (P)"><i class="sw pathways"></i>Pathway</button>
            <button data-tool="sidewalks" title="Draw a sidewalk"><i class="sw sidewalks"></i>Sidewalk</button>
            <button data-tool="areas" title="Sketch an area (export only)"><i class="sw areas"></i>Area</button>
          </div>
          <div class="row">
            <button id="undo" disabled title="Undo (Ctrl/Cmd+Z)">Undo</button>
            <button id="redo" disabled title="Redo (Shift+Ctrl/Cmd+Z)">Redo</button>
            <button id="reload" title="Reload the map from the robot (keeps nothing you haven't sent)">Reload</button>
            <button id="fit">Zoom to map</button>
          </div>
          <p class="hint" id="tool-hint">Click a shape to select it. Drag its corners; drag a midpoint to add one; right-click a corner to delete it.</p>
        </section>

        <section id="selection" hidden>
          <h2>Selected</h2>
          <div id="selection-body"></div>
        </section>

        <section>
          <h2>Changes <span id="change-count" class="count">0</span></h2>
          <ul id="ops" class="ops"></ul>
          <div class="row">
            <button id="apply" class="primary" disabled>Apply to robot</button>
            <button id="export">Export GeoJSON</button>
            <label class="button">Import<input id="import" type="file" accept=".geojson,.json,application/geo+json" hidden /></label>
            <button id="discard-all" disabled title="Throw away every change not yet sent">Discard all</button>
          </div>
          <p class="hint" id="apply-hint"></p>
          <div id="undo-apply-box" class="row" hidden></div>
        </section>

        <section>
          <h2>History</h2>
          <p class="hint" style="margin-top:0">The robot's map as it was before each apply. Reverting loads it as changes for you to review and apply.</p>
          <ul id="history" class="plans"></ul>
        </section>

        <section>
          <h2>Plans</h2>
          <ul id="plans" class="plans"></ul>
          <div class="row"><button id="new-plan">New plan</button></div>
          <div id="plan-editor" hidden></div>
        </section>

        <section>
          <h2>View</h2>
          <div class="heat">
            <label class="check"><input type="checkbox" id="heat-on" /> Wear heat map (estimated)</label>
            <div class="row" style="margin-top:0">
              <select id="heat-days">
                <option value="0">All history</option>
                <option value="365">Last year</option>
                <option value="90">Last 90 days</option>
                <option value="30">Last 30 days</option>
              </select>
              <button id="heat-refresh" title="Read the run history from the robot again">↻</button>
            </div>
            <div id="heat-info" class="hint"></div>
          </div>
          <div id="layers" class="layers"></div>
          <div class="nudge">
            <span>Imagery alignment</span>
            <div class="nudge-grid">
              <span></span><button data-nudge="0,1" title="Move map shapes north">▲</button><span></span>
              <button data-nudge="-1,0" title="West">◀</button><button data-nudge="reset" title="Reset">·</button><button data-nudge="1,0" title="East">▶</button>
              <span></span><button data-nudge="0,-1" title="South">▼</button><span></span>
            </div>
            <span id="offset" class="hint">0.00 m E, 0.00 m N</span>
          </div>
          <p class="hint">Satellite photos are often 1–3 m off from RTK. Alignment only moves what you see; it is never sent to the robot.</p>
        </section>
        <footer id="message" role="status"></footer>
      </aside>
      <div id="map"></div>
    </div>
`;
