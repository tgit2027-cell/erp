// Test-only DOM and CSS loader; no app authentication or runtime configuration.
import { registerHooks } from "node:module";
import { JSDOM } from "jsdom";
import React from "react";

registerHooks({
  load(url, context, nextLoad) {
    if (url.endsWith(".css")) return { format: "module", source: "", shortCircuit: true };
    return nextLoad(url, context);
  },
});

const dom = new JSDOM("<!doctype html><html><body></body></html>", {
  url: "https://synthetic.invalid/",
  pretendToBeVisual: true,
});
for (const name of ["window", "document", "navigator", "HTMLElement", "HTMLInputElement", "HTMLAnchorElement", "Node", "Event", "MouseEvent"]) {
  Object.defineProperty(globalThis, name, { configurable: true, value: dom.window[name] });
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
// Node's test JSX transform may use the classic runtime; Vite uses automatic JSX.
globalThis.React = React;
