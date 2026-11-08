import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./style.css";
// Friendly look and feel; keep it last so it layers over every section style.
import "./theme-refresh.css";
// Each area's own colour on the landing-page tiles.
import "./area-colours.css";

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
