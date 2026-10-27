import test from "node:test";
import assert from "node:assert/strict";
import { browserHarness } from "./browser-harness.mjs";

const sentinel = "TEST_ONLY_PRIVATE_FINANCE_COST";
for (const width of [1280, 390]) {
  test(`shared Finance status and existing destinations with mocked APIs at ${width}px`, async () => {
    const manager = width === 1280;
    const b = await browserHarness({
      width, initialHash: "#/finance/main",
      readyExpression: '!!document.querySelector("#finance-status-title")',
      mockApi: async (request, url) => {
        assert.equal(request.method, "GET", "Status/navigation must not submit anything.");
        if (url.pathname === "/api/auth/csrf/") return { data: { csrfToken: "TEST_ONLY" } };
        if (url.pathname === "/api/auth/me/") return { data: { authenticated: true,
          username: "TEST_ONLY_STATUS", is_staff: false, is_superuser: false, can_manage_report_panels: manager } };
        if (url.pathname === "/api/organization/") {
          const unit = { id: 1, code: "TEST", name: "TEST ONLY unit", theme_palette: "tivoli" };
          return { data: { company: { name: "TEST ONLY company", code: "TEST" },
            units: [unit], selected_unit: unit, locations: [] } };
        }
        if (url.pathname === "/api/favorites/") return { data: {
          favorites: [{ destination: "finance", label: "Finance & Accounting" }],
          available_destinations: [{ destination: "finance", label: "Finance & Accounting" }],
        } };
        return { status: 403, data: { detail: "TEST ONLY existing access denied", unit_cost: sentinel } };
      },
    });
    try {
      await b.wait('document.getAnimations().every(a=>a.playState!=="running" || a.effect.getTiming().iterations===Infinity)');
      assert.equal(await b.evaluate('!!document.querySelector(\'[data-workspace-view="finance/main"] a[href="#/governance/report-panels"]\')'), manager);
      assert.equal(await b.evaluate('document.querySelector(\'[data-workspace-view="finance/main"]\').querySelectorAll("form,input,select,textarea").length'), 0);
      assert.equal(await b.evaluate('document.querySelectorAll(\'a[href*="/lab"],a[href*="finance-draft"]\').length'), 0);
      const geometry = await b.evaluate('({client:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth})');
      assert.ok(geometry.scroll <= geometry.client, JSON.stringify(geometry));
      const textColors = await b.evaluate(`(()=>{
        const ctx=document.createElement("canvas").getContext("2d",{willReadFrequently:true});
        const rgba=c=>{ctx.clearRect(0,0,1,1);ctx.fillStyle=c;ctx.fillRect(0,0,1,1);return [...ctx.getImageData(0,0,1,1).data]};
        const blend=(a,b)=>a.slice(0,3).map((v,i)=>v*a[3]/255+b[i]*(1-a[3]/255));
        return [...document.querySelector('[data-workspace-view="finance/main"]').querySelectorAll("h2,h3,p,li,a")].map(n=>{
          const ancestors=[];for(let p=n;p;p=p.parentElement)ancestors.unshift(p);
          let bg=[255,255,255];for(const p of ancestors)bg=blend(rgba(getComputedStyle(p).backgroundColor),bg);
          return [blend(rgba(getComputedStyle(n).color),bg),bg];
        });
      })()`);
      const luminance = rgb => rgb.map(v => v / 255).map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4)
        .reduce((sum, value, i) => sum + value * [.2126, .7152, .0722][i], 0);
      const minContrast = Math.min(...textColors.map(([fg, bg]) => {
        const values = [luminance(fg), luminance(bg)].sort((a, b) => a - b);
        return (values[1] + .05) / (values[0] + .05);
      }));
      assert.ok(minContrast >= 4.5, `New status text contrast: ${minContrast.toFixed(2)}:1`);
      await b.evaluate('document.querySelector(\'[data-workspace-view="finance/main"] a[href="#/retail/quote"]\').focus()');
      await b.key("Tab");
      assert.equal(await b.evaluate('document.activeElement.getAttribute("href")'), "#/retail/collections");
      const focus = await b.evaluate('({style:getComputedStyle(document.activeElement).outlineStyle,width:getComputedStyle(document.activeElement).outlineWidth})');
      assert.notEqual(focus.style, "none");
      assert.ok(parseFloat(focus.width) >= 2);
      await b.screenshot(`finance-status-${width}`, "integration-readiness");
      await b.key("Enter");
      await b.wait('document.querySelector(".page-heading h1")?.textContent==="Collection follow-up"');
      await b.evaluate("history.back()");
      await b.wait('!!document.querySelector("#finance-status-title")');
      await b.evaluate("history.forward()");
      await b.wait('document.querySelector(".page-heading h1")?.textContent==="Collection follow-up"');
      assert.equal(await b.evaluate(`document.body.textContent.includes(${JSON.stringify(sentinel)})`), false);
      assert.ok(b.calls.every(call => call.method === "GET"));
      assert.ok(b.calls.every(call => !JSON.stringify(call).includes("/lab/")));
      console.log(`${width}px: status/no lab links or forms; existing manager gate; keyboard focus/Enter, back/forward, denied collections; ${JSON.stringify(geometry)}; minimum text contrast ${minContrast.toFixed(2)}:1; GET only. Mock APIs, not real sign-in.`);
      b.assertSafe();
    } finally { await b.close(); }
  });
}
