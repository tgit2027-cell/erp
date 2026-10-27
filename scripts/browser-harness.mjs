// Dependency-free Chromium/CDP adapter. Default: mocked APIs. Optional explicit
// loopback real-backend proxy: unchanged Django cookies/CSRF/responses, no mocks.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, rm, mkdir, writeFile, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve, extname } from "node:path";
import { once } from "node:events";
import { createServer, request as httpRequest } from "node:http";

export async function browserHarness({ width = 1280, height = 900, mockApi, mockPosts = [], initialHash = "",
  realBackendOrigin, allowedRealWrites = [], beforeRealResponse, beforeAssetResponse, assetDirectory = "dist", readyExpression,
  apiPrefix = "/api/", entryPath = "/", documentPaths = [] } = {}) {
  if (realBackendOrigin && (mockApi || mockPosts.length)) throw new Error("Real backend cannot be mixed with mocked APIs");
  if (realBackendOrigin && !/^http:\/\/127\.0\.0\.1:\d+$/.test(realBackendOrigin)) throw new Error("Real acceptance backend must be explicit loopback HTTP");
  const calls = [], unexpected = [], errors = [], external = [], dialogs = [];
  const units = [
    { id: 1, code: "TVL", name: "TEST ONLY Tivoli", theme_palette: "tivoli" },
    { id: 2, code: "RBB", name: "TEST ONLY Roche", theme_palette: "roche" },
    { id: 3, code: "NAT", name: "TEST ONLY Natuzzi", theme_palette: "natuzzi" },
  ];
  let selected = 1;
  const profile = await mkdtemp(join(tmpdir(), "tivoli-browser-"));
  const purchasing = { destination: "purchasing", label: "Purchasing Workbench" };
  const legacyDestinations = [
    ["retail", "Retail & Sales"], ["procurement", "Procurement & Imports"],
    ["inventory", "Inventory & Warehouses"], ["delivery", "Delivery & After-Sales"],
    ["governance", "Governance & Administration"], ["reports", "Reports"],
    ["purchasing", "Purchasing Workbench"],
  ].map(([destination, label]) => ({ destination, label }));
  const functionKeys = [
    "retail.quote", "retail.actions", "retail.register",
    "procurement.commitments", "procurement.progress", "procurement.receipt",
    "procurement.packing", "procurement.registers", "procurement.suppliers",
    "inventory.locations", "inventory.receipt", "inventory.commitments",
    "inventory.progress", "inventory.packing", "inventory.registers", "inventory.transfers", "inventory.capacity",
    "delivery.quote", "delivery.preparation", "delivery.handover", "delivery.quotes", "delivery.storage",
    "delivery.reservations", "delivery.deliveries", "delivery.returns",
    "governance.presentation", "governance.purchasing-config", "governance.reporting",
    "governance.report-panels", "governance.capacity-admin", "governance.capacity-audit", "governance.transfer-permissions",
    "purchasing.quote", "purchasing.transactions", "purchasing.registers",
  ].map((destination) => ({ destination, label: destination.split(".").at(-1) }));
  const availableDestinations = [...legacyDestinations, ...functionKeys];
  const assetRoot = resolve(assetDirectory);
  const server = createServer(async (req, res) => {
    if (!req.url.startsWith(apiPrefix)) {
      const pathname = new URL(req.url, "http://fixture.invalid").pathname;
      const file = resolve(assetRoot, `.${pathname === "/" || pathname === entryPath || documentPaths.includes(pathname) ? "/index.html" : pathname}`);
      if (!file.startsWith(`${assetRoot}/`)) { res.writeHead(403).end(); return; }
      try {
        const body = await readFile(file);
        // Optional asset-transport fault only. Never modifies business APIs,
        // authentication or upstream real-backend responses.
        const fault = await beforeAssetResponse?.(pathname);
        if (fault?.status) {
          res.writeHead(fault.status, { "Content-Type": "text/plain", "Cache-Control": "no-store" }).end();
          return;
        }
        const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css" };
        res.writeHead(200, { "Content-Type": types[extname(file)] || "application/octet-stream", "Cache-Control": "no-store" });
        res.end(body);
      } catch { res.writeHead(404).end(); }
      return;
    }
          const url = new URL(req.url, "http://fixture.invalid");
          calls.push({ method: req.method, path: url.pathname, query: url.search });
          if (realBackendOrigin) {
            const call = calls.at(-1);
            const upstream = httpRequest(`${realBackendOrigin}${req.url}`, {
              method: req.method, headers: { ...req.headers },
            }, response => {
              const chunks = [];
              response.on("data", chunk => chunks.push(chunk));
              response.on("end", async () => {
                call.status = response.statusCode;
                try {
                  // Gate delivery only; Django has actually processed and committed.
                  // Never replace an upstream status, body, session cookie or CSRF result.
                  await beforeRealResponse?.(call);
                  res.writeHead(response.statusCode, response.headers);
                  res.end(Buffer.concat(chunks));
                } catch { res.writeHead(502); res.end(); }
              });
            });
            upstream.on("error", () => { res.writeHead(502); res.end(); });
            req.pipe(upstream);
            return;
          }
          let data, status = 200;
          const get = req.method === "GET";
           const mocked = mockApi ? await mockApi(req, url) : null;
           if (mocked) ({ data, status = 200 } = mocked);
           else if (get && url.pathname === "/api/auth/csrf/") data = { csrfToken: "TEST_ONLY_NO_CREDENTIAL" };
          else if (get && url.pathname === "/api/auth/me/") data = {
            authenticated: true, username: "TEST_ONLY_BROWSER", is_staff: false,
            is_superuser: false, can_manage_report_panels: false,
          };
          else if (get && url.pathname === "/api/organization/") data = {
            company: { name: "TEST ONLY company", code: "TEST" }, units,
            selected_unit: units[selected - 1], locations: [],
          };
          else if (get && url.pathname === "/api/favorites/") data = {
            available_destinations: availableDestinations, favorites: [purchasing],
          };
          else if (req.method === "POST" && /^\/api\/units\/[123]\/select\/$/.test(url.pathname)) {
            selected = Number(url.pathname.split("/")[3]);
            data = { selected_unit: units[selected - 1] };
          } else if (get && url.pathname === "/api/stock-balances/" &&
            url.searchParams.get("unit") === String(selected)) data = {
            results: [{
              id: selected, item_code: `TEST-LEDGER-${selected}`, item_description: "TEST ONLY item",
              brand_name: units[selected - 1].name, variant_sku: "TEST-SKU",
              variant_description: "TEST ONLY variant", location_code: `TEST-LOC-${selected}`,
              location_name: "TEST ONLY warehouse", unit_code: units[selected - 1].code,
              unit_name: units[selected - 1].name, quantity: "2.500", is_synthetic: true,
            }],
            pagination: { page: 1, page_size: 50, total_count: 1, total_pages: 1, next_page: null, previous_page: null },
          };
          else if (get && /^\/api\/purchasing\/(quotes|receipts|storage-charges)\/$/.test(url.pathname) &&
            url.searchParams.get("business_unit_id") === String(selected)) {
            status = 403; data = { detail: "TEST ONLY existing access denied" };
          } else if (get && /^\/api\/reports\/(purchase_history\/receipts|inventory\/reservations|sales\/deliveries)\/$/.test(url.pathname) &&
            url.searchParams.get("unit") === String(selected)) {
            status = 403; data = { detail: "TEST ONLY existing access denied" };
          } else if (get && url.pathname === "/api/warehouse-capacity/") {
            status = 403; data = { detail: "TEST ONLY capacity unassigned" };
          } else {
            unexpected.push({ method: req.method, path: req.url });
            status = 403; data = { detail: "TEST ONLY unexpected request blocked" };
          }
          res.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store" });
          res.end(JSON.stringify(data));
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const origin = `http://127.0.0.1:${server.address().port}`;
  const child = spawn(process.env.CHROMIUM_PATH || "chromium", [
    "--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
    "--no-first-run", "--disable-background-networking", "--disable-component-update",
    "--disable-extensions", "--disable-sync", "--no-proxy-server",
    "--disable-renderer-backgrounding", "--disable-background-timer-throttling",
    "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
    "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0",
    `--user-data-dir=${profile}`, "about:blank",
  ], { stdio: ["ignore", "ignore", "pipe"] });
  let socket;
  try {
    const endpoint = await new Promise((resolve, reject) => {
      // stderr is a byte stream; the endpoint may span multiple data chunks.
      // Allow bounded cold startup on the shared development runner.
      let startupStderr = "";
      const timer = setTimeout(() => reject(new Error("Chromium startup timed out (45 seconds)")), 45000);
      child.once("error", (error) => { clearTimeout(timer); reject(error); });
      child.once("exit", (code) => { clearTimeout(timer); reject(new Error(`Chromium exited ${code}`)); });
      child.stderr.on("data", (chunk) => {
        startupStderr = (startupStderr + chunk.toString()).slice(-32768);
        const match = startupStderr.match(/DevTools listening on (ws:\/\/\S+)\r?\n/);
        if (match) { clearTimeout(timer); resolve(match[1]); }
      });
    });
    socket = new WebSocket(endpoint);
    await once(socket, "open");
    let sequence = 0, session, loadSequence = 0;
    const pending = new Map();
    const send = (method, params = {}, sessionId = session) => new Promise((resolve, reject) => {
      const id = ++sequence;
      const timer = setTimeout(() => { pending.delete(id); reject(new Error(`CDP timeout: ${method} ${method === "Input.insertText" ? "<redacted input>" : JSON.stringify(params).slice(0, 200)}`)); }, 10000);
      pending.set(id, { resolve, reject, timer });
      socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
    });
    socket.onmessage = ({ data }) => {
      const message = JSON.parse(data);
      if (process.env.BROWSER_DEBUG && message.method) console.error(message.method, JSON.stringify(message.params).slice(0, 160));
      if (message.id) {
        const item = pending.get(message.id);
        if (!item) return;
        clearTimeout(item.timer); pending.delete(message.id);
        if (message.error) item.reject(new Error(JSON.stringify(message.error)));
        else item.resolve(message.result);
      } else if (message.method === "Page.loadEventFired") {
        loadSequence += 1;
      } else if (message.method === "Page.javascriptDialogOpening") {
        dialogs.push(message.params);
      } else if (message.method === "Runtime.exceptionThrown") {
        errors.push(message.params.exceptionDetails.text);
      } else if (message.method === "Fetch.requestPaused") {
        const { requestId, request } = message.params;
        const local = request.url.startsWith(`${origin}/`) || request.url.startsWith("data:");
        if (!local) external.push(request.url);
        send(local ? "Fetch.continueRequest" : "Fetch.failRequest",
          local ? { requestId } : { requestId, errorReason: "BlockedByClient" })
          .catch((error) => errors.push(error.message));
      }
    };
    const { targetId } = await send("Target.createTarget", { url: "about:blank" });
    session = (await send("Target.attachToTarget", { targetId, flatten: true })).sessionId;
    await send("Target.activateTarget", { targetId });
    await send("Runtime.enable");
    await send("Page.enable");
    await send("Fetch.enable", { patterns: [{ urlPattern: "*" }] });
    await send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: false });
    const evaluate = async (expression) => {
      const result = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
      if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
      return result.result.value;
    };
    const wait = async (expression) => {
      const until = Date.now() + 10000;
      while (Date.now() < until) {
        if (await evaluate(expression)) return;
        await new Promise((resolve) => setTimeout(resolve, 30));
      }
      throw new Error(`Browser assertion timed out: ${expression}`);
    };
    await send("Page.navigate", { url: `${origin}${entryPath}${initialHash}` });
    await wait(readyExpression ?? (realBackendOrigin ? '!!document.querySelector("#username")' : initialHash
      ? '!!document.querySelector("[data-workspace-view], [data-workspace-unavailable]")'
      : 'document.querySelector("h1")?.textContent === "Overview"'));
    return {
      origin, calls, unexpected, errors, external, evaluate, wait, dialogs,
      async handleDialog(accept) { await send("Page.handleJavaScriptDialog", { accept }); },
      async reload() { await send("Page.reload"); },
      async reload() {
        const previous = loadSequence;
        await send("Page.reload");
        const until = Date.now() + 10000;
        while (loadSequence === previous && Date.now() < until) {
          await new Promise(resolve => setTimeout(resolve, 20));
        }
        assert.ok(loadSequence > previous, "Reload completed a fresh document load");
      },
      async navigate(hash) {
        assert.ok(hash.startsWith("#/"), "Only local function routes in this harness");
        await send("Page.navigate", { url: `${origin}/${hash}` });
      },
      async history(offset) {
        assert.ok(offset === -1 || offset === 1);
        const history = await send("Page.getNavigationHistory");
        const entry = history.entries[history.currentIndex + offset];
        assert.ok(entry, "Requested browser history entry exists");
        await send("Page.navigateToHistoryEntry", { entryId: entry.id });
      },
      async text(text) { await send("Input.insertText", { text }); },
      async uploadFile(selector, filePath) {
        // Real Chromium file input, not a fabricated File/change event.
        const { root } = await send("DOM.getDocument");
        const { nodeId } = await send("DOM.querySelector", { nodeId: root.nodeId, selector });
        assert.ok(nodeId, "File input exists");
        await send("DOM.setFileInputFiles", { nodeId, files: [resolve(filePath)] });
      },
      async accessibility() { return (await send("Accessibility.getFullAXTree")).nodes; },
      async key(key, shift = false) {
        const codes = { Tab: [9, "Tab"], Enter: [13, "Enter"], Backspace: [8, "Backspace"], " ": [32, "Space"], ArrowDown: [40, "ArrowDown"], ArrowUp: [38, "ArrowUp"], Home: [36, "Home"], End: [35, "End"] };
        const [windowsVirtualKeyCode, code] = codes[key];
        const params = { key, code, windowsVirtualKeyCode, modifiers: shift ? 8 : 0 };
        const text = key === "Enter" ? "\r" : key === " " ? " " : "";
        await send("Input.dispatchKeyEvent", {
          type: text ? "keyDown" : "rawKeyDown", ...params,
          ...(text ? { text, unmodifiedText: text } : {}),
        });
        await send("Input.dispatchKeyEvent", { type: "keyUp", ...params });
        await evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))");
      },
      async click(selector) {
        const settle = () => evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))");
        await settle();
        const sidebarTarget = await evaluate(`!!document.querySelector(${JSON.stringify(selector)})?.closest(".sidebar")`);
        // A real pointer leaves the compact navigation flyout before reaching
        // page controls. mousePressed alone does not model that movement.
        if (!sidebarTarget) {
          await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: width - 8, y: height - 8 });
          await settle();
        }
        await evaluate(`(() => {
          const node = document.querySelector(${JSON.stringify(selector)});
          if (!node) throw new Error("Missing target");
          node.scrollIntoView({block:"center",inline:"nearest"});
        })()`);
        await settle();
        const point = await evaluate(`(() => {
          const node = document.querySelector(${JSON.stringify(selector)});
          const r = node.getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2};
        })()`);
        await send("Input.dispatchMouseEvent", { type: "mouseMoved", ...point });
        await send("Input.dispatchMouseEvent", { type: "mousePressed", button: "left", clickCount: 1, ...point });
        await send("Input.dispatchMouseEvent", { type: "mouseReleased", button: "left", clickCount: 1, ...point });
      },
      async screenshot(name, directory = "sidebar-browser") {
        const { data } = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
        await mkdir(`generated-artifacts/${directory}`, { recursive: true });
        await writeFile(`generated-artifacts/${directory}/${name}.png`, Buffer.from(data, "base64"));
      },
      assertSafe() {
        assert.deepEqual(unexpected, [], "No unauthorized or unrecognized API paths");
        assert.deepEqual(external, [], "No external page requests");
        assert.deepEqual(errors, [], "No uncaught browser exceptions");
        if (realBackendOrigin) {
          assert.ok(calls.every(call => call.method === "GET" || allowedRealWrites.includes(`${call.method} ${call.path}`)),
            "Real backend writes are explicitly allowlisted and disposable only");
          return;
        }
        assert.ok(calls.every((call) => call.method === "GET" ||
          (call.method === "POST" && (/^\/api\/units\/[123]\/select\/$/.test(call.path) || mockPosts.includes(call.path)))),
        "Only explicit allowlisted in-memory mock POSTs; no real transactions or grants");
      },
      async close() {
        socket.close(); child.kill("SIGTERM");
        await once(child, "exit");
        server.closeAllConnections();
        await new Promise(resolve => server.close(resolve));
        // Chromium helpers can finish flushing the disposable profile just
        // after the main process exits. Retry cleanup only, never HTTP actions.
        await rm(profile, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
      },
    };
  } catch (error) {
    socket?.close(); child.kill("SIGTERM"); server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
    await rm(profile, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
    throw error;
  }
}
