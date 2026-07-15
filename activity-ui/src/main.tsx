import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { resolveSession } from "./lib/session";
import "./styles/app.css";

const root = ReactDOM.createRoot(document.getElementById("root")!);

// A brief splash while the Discord handshake (OAuth) resolves the session.
root.render(
  <div
    className="boot"
    style={{
      display: "flex", alignItems: "center", justifyContent: "center",
      height: "100vh", fontFamily: "serif", letterSpacing: "0.05em",
      opacity: 0.7,
    }}
  >
    Summoning the Oracle&hellip;
  </div>,
);

resolveSession().then((session) => {
  root.render(
    <React.StrictMode>
      <App session={session} />
    </React.StrictMode>,
  );
});
