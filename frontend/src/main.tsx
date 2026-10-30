import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";
import { STATIC, loadImageManifest } from "./lib/staticDemo";

const render = () =>
  ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );

// The static demo first learns which images it ships, so it never asks for the others.
if (STATIC) loadImageManifest().finally(render);
else render();
