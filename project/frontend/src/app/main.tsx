import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "./fonts";
import "./index.css";

const root = document.getElementById("root");
if (!root) throw new Error("missing #root");

createRoot(root).render(
  <StrictMode>
    <main className="p-8">
      <h1 className="sp-display">SuwaPath</h1>
    </main>
  </StrictMode>,
);
