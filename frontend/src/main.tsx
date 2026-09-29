import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
// Inter is bundled with the app (no third-party font request); the browser
// downloads only the character ranges a page uses.
import "@fontsource-variable/inter";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);

