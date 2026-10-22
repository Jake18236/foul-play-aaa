// ==UserScript==
// @name         Foul Play Advisor
// @namespace    foul-play-advisor
// @version      1.4.0
// @description  Read-only Foul Play recommendations in Pokémon Showdown
// @match        https://play.pokemonshowdown.com/*
// @run-at       document-start
// @grant        GM_xmlhttpRequest
// @connect      127.0.0.1
// ==/UserScript==

(() => {
  "use strict";
  const SERVER = "http://127.0.0.1:8765/";
  const TOKEN = "fpa-" + Math.random().toString(36).slice(2);
  let panel, status, result, roomLabel;
  let activeRoom = "", observedRoom = "", lastSeq = -1;
  const requests = new Map();
  const queue = [];
  let sending = false;
  const lastForwardedFrame = new Map();

  function api(method, data, callback) {
    GM_xmlhttpRequest({
      method, url: SERVER,
      data: data ?? undefined,
      headers: method === "POST" ? {"Content-Type": "text/plain;charset=UTF-8"} : {},
      timeout: 10000,
      onload: response => callback?.(null, response),
      onerror: () => callback?.(new Error("Cannot connect to Python server")),
      ontimeout: () => callback?.(new Error("Python server timed out"))
    });
  }

  function flush() {
    if (sending || !queue.length) return;
    sending = true;
    api("POST", queue[0], error => {
      sending = false;
      if (error) {
        setStatus(error.message);
        setTimeout(flush, 2000);
      } else {
        queue.shift();
        flush();
      }
    });
  }

  function receiveProtocol(payload) {
    if (typeof payload !== "string") return;
    const frames = payload.replace(/\r/g, "").split(/(?=^>)/m);
    for (const frame of frames) {
      if (!/^>battle-[^\n]+/.test(frame)) continue;
      const room = frame.match(/^>(battle-[^\n]+)/)?.[1]?.trim();
      if (!room) continue;
      observedRoom = room;

      for (const line of frame.split("\n")) {
        if (!line.startsWith("|request|")) continue;
        try {
          requests.set(room, JSON.parse(line.slice(9)));
        } catch (_) {}
      }

      if (lastForwardedFrame.get(room) === frame) continue;
      lastForwardedFrame.set(room, frame);
      queue.push(frame);
    }
    flush();
  }

  window.addEventListener("message", event => {
    if (event.source !== window) return;
    const m = event.data;
    if (m?.source !== "fpa-page-bridge" || m.token !== TOKEN) return;
    if (m.type === "protocol") receiveProtocol(m.data);
  });

  function installReceiveHook() {
    const code = `(() => {
      const token = ${JSON.stringify(TOKEN)};
      const marker = Symbol.for("foul-play-advisor.receive-hook");

      function install() {
        const app = window.app;
        if (!app || typeof app.receive !== "function" || app.receive[marker]) return;
        const original = app.receive;

        function wrappedReceive(...args) {
          try {
            for (const arg of args) {
              if (typeof arg === "string" && /(?:^|\\n)>battle-/.test(arg)) {
                window.postMessage({
                  source: "fpa-page-bridge",
                  token,
                  type: "protocol",
                  data: arg
                }, "*");
              }
            }
          } catch (e) {
            console.warn("[FPA]", e);
          }
          return Reflect.apply(original, this, args);
        }

        wrappedReceive[marker] = true;
        app.receive = wrappedReceive;
      }

      install();
      setInterval(install, 500);
    })();`;
    const script = document.createElement("script");
    script.textContent = code;
    (document.documentElement || document.head || document).appendChild(script);
    script.remove();
  }

  function visibleRoom() {
    const match = (location.hash + " " + location.pathname)
      .match(/battle-[a-z0-9-]+/i);
    return match ? match[0] : observedRoom;
  }

  function prettyMove(id, data) {
    const move = data?.active?.[0]?.moves?.find(m =>
      m.id === id ||
      m.move?.toLowerCase().replace(/[^a-z0-9]/g, "") === id
    );
    if (move?.move) return move.move;
    return id.replace(/([a-z])([A-Z])/g, "$1 $2")
      .replace(/\b\w/g, c => c.toUpperCase());
  }

  function readable(command, room) {
    const data = requests.get(room);
    const raw = String(command || "").trim();
    const base = raw.replace(/\|\d+$/, "");

    const sw = base.match(/^\/(?:choose\s+)?switch\s+(\d+)/i);
    if (sw) {
      const pokemon = data?.side?.pokemon?.[Number(sw[1]) - 1];
      const name = pokemon?.details?.split(",")[0] ||
        pokemon?.ident?.split(": ")[1];
      return name ? "Switch to " + name : "Switch to slot " + sw[1];
    }

    const mv = base.match(/^\/(?:choose\s+)?move\s+(.+)$/i);
    if (mv) {
      const tokens = mv[1].trim().split(/\s+/);
      const id = tokens.shift();
      const idx = Number(id);
      const actualId = Number.isInteger(idx) && idx > 0
        ? data?.active?.[0]?.moves?.[idx - 1]?.id || id
        : id;
      const name = prettyMove(actualId, data);
      const suffix = tokens.includes("terastallize")
        ? " (Terastallize)"
        : tokens.includes("mega")
        ? " (Mega Evolve)"
        : tokens.includes("zmove")
        ? " (Z-Move)"
        : "";
      return "Use " + name + suffix;
    }

    if (base.startsWith("/team ")) {
      return "Team preview: " + base.slice(6).split("|")[0];
    }
    return base;
  }

  function setStatus(text) {
    if (status) status.textContent = text;
  }

  function poll() {
    const room = visibleRoom();
    if (room !== activeRoom) {
      activeRoom = room;
      lastSeq = -1;
      if (roomLabel) roomLabel.textContent = room || "No active battle";
      if (result) result.textContent = "";
      setStatus(room ? "Waiting for analysis" : "Waiting for battle");
    }

    api("GET", null, (error, response) => {
      if (error) {
        setStatus(error.message);
        return;
      }
      let data;
      try {
        data = JSON.parse(response.responseText);
      } catch (_) {
        setStatus("Invalid server response");
        return;
      }
      if (!data.room) return;
      if (!activeRoom && observedRoom === data.room) {
        activeRoom = data.room;
        if (roomLabel) roomLabel.textContent = activeRoom;
      }
      if (data.room !== activeRoom || data.seq === lastSeq) return;
      lastSeq = data.seq;

      if (data.status === "ready") {
        if (result) result.textContent = readable(data.text, activeRoom);
        setStatus("Recommendation");
      } else if (data.status === "thinking") {
        if (result) result.textContent = "";
        setStatus("Thinking...");
      } else if (data.status === "finished") {
        if (result) result.textContent = "";
        setStatus("Battle finished");
      } else if (data.status === "error") {
        if (result) result.textContent = data.text || "Check Python terminal";
        setStatus("Error");
      }
    });
  }

  function createPanel() {
    if (panel || !document.body) return;
    panel = document.createElement("div");
    panel.style.cssText = `
      position:fixed;
      right:16px;
      top:100px;
      width:245px;
      z-index:999999;
      background:#242424;
      color:#eee;
      border:1px solid #555;
      border-radius:8px;
      padding:12px;
      box-shadow:0 4px 15px #0006;
      font:13px Arial,sans-serif;
    `;

    const heading = document.createElement("div");
    heading.textContent = "FOUL PLAY ADVISOR";
    heading.style.cssText =
      "font-weight:bold;cursor:move;user-select:none;margin-bottom:8px;";
    roomLabel = document.createElement("div");
    roomLabel.style.cssText =
      "font-size:11px;color:#aaa;overflow-wrap:anywhere;margin-bottom:8px;";
    status = document.createElement("div");
    status.style.cssText =
      "font-size:12px;color:#bbb;margin-bottom:8px;";
    result = document.createElement("div");
    result.style.cssText =
      "font-size:15px;font-weight:bold;overflow-wrap:anywhere;";

    panel.append(heading, roomLabel, status, result);
    document.body.appendChild(panel);

    let drag = false, x = 0, y = 0;
    heading.addEventListener("mousedown", e => {
      drag = true;
      const rect = panel.getBoundingClientRect();
      x = e.clientX - rect.left;
      y = e.clientY - rect.top;
      panel.style.left = rect.left + "px";
      panel.style.top = rect.top + "px";
      panel.style.right = "auto";
      e.preventDefault();
    });
    document.addEventListener("mousemove", e => {
      if (!drag) return;
      panel.style.left = (e.clientX - x) + "px";
      panel.style.top = (e.clientY - y) + "px";
    });
    document.addEventListener("mouseup", () => { drag = false; });
    poll();
  }

  installReceiveHook();
  const timer = setInterval(() => {
    createPanel();
    if (panel) clearInterval(timer);
  }, 100);
  setInterval(poll, 750);
})();