// Standalone entry: the Unbubble app UI served by the helper (and `npm run dev`).
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { UnbubbleApp } from "./App";
import "./styles.css";

const el = document.getElementById("unbubble");
if (el) {
  createRoot(el).render(
    <StrictMode>
      <UnbubbleApp />
    </StrictMode>,
  );
}
