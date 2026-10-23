// ==UserScript==
// @name         Foul Play Advisor
// @namespace    foul-play-advisor
// @version      1.7.0
// @description  Battle recovery, search analysis, and optional move controls
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
  let rationale, evaluation, evaluationBar, evaluationMarker;
  let choices, opponentMoves, opponentTeam, teamEditor;
  let executeButton, autoToggle, editTeamButton;
  let currentAnalysis = null;
  let currentModel = null;
  let modelDraft = [];
  let latestRecommendation = null;
  let autoTimer = null;
  let pendingActionKey = null;
  const sentActions = new Set();
  let activeRoom = "";
  let observedRoom = "";
  let lastSeq = -1;

  const requests = new Map();
  const queue = [];
  const lastForwardedFrame = new Map();

  let sending = false;

  function api(method, data, callback, extraHeaders = {}) {
    GM_xmlhttpRequest({
      method,
      url: SERVER,
      data: data ?? undefined,
      headers: {
        ...(method === "POST"
          ? {"Content-Type": "text/plain;charset=UTF-8"}
          : {}),
        ...extraHeaders
      },
      timeout: 10000,
      onload: response => callback?.(
        response.status >= 400
          ? new Error(`Advisor server returned HTTP ${response.status}`)
          : null,
        response
      ),
      onerror: () =>
        callback?.(new Error("Cannot connect to Python server")),
      ontimeout: () =>
        callback?.(new Error("Python server timed out"))
    });
  }

  function setStatus(text) {
    if (status) status.textContent = text;
  }

  function flush() {
    if (sending || !queue.length) return;

    sending = true;

    const item = queue[0];
    api("POST", item.frame, error => {
      sending = false;

      if (error) {
        setStatus(error.message);
        setTimeout(flush, 2000);
        return;
      }

      const index = queue.indexOf(item);
      if (index >= 0) queue.splice(index, 1);
      flush();
    }, item.recovered ? {"X-FPA-Recovery": "1"} : {});
  }

  function saveRequests(room, frame) {
    for (const line of frame.split("\n")) {
      if (!line.startsWith("|request|")) continue;

      try {
        requests.set(room, JSON.parse(line.slice(9)));
      } catch (_) {}
    }
  }

  function forwardFrame(room, frame) {
    if (!room || !frame) return;

    observedRoom = room;
    saveRequests(room, frame);

    if (lastForwardedFrame.get(room) === frame) return;

    lastForwardedFrame.set(room, frame);
    queue.push({room, frame, recovered: false});

    flush();
  }

  function receiveProtocol(payload) {
    if (typeof payload !== "string") return;

    const frames = payload
      .replace(/\r/g, "")
      .split(/(?=^>)/m);

    for (const frame of frames) {
      if (!/^>battle-[^\n]+/.test(frame)) continue;

      const room = frame.match(/^>(battle-[^\n]+)/)?.[1]?.trim();
      if (!room) continue;

      forwardFrame(room, frame);
    }
  }

  function receiveRecoveredHistory(message) {
    const room = message.room;
    const history = message.history;

    if (!room || typeof history !== "string") return;

    const normalized = history.replace(/\r/g, "").trim();

    if (!normalized) return;

    /*
     * Send recovered history as a single ordered frame.
     *
     * This is important because splitting history into individual
     * protocol lines could cause the Python server to process
     * incomplete battle events.
     */

    const frame = ">" + room + "\n" + normalized;

    /*
     * Recovery messages are queued in front of pending live
     * messages for the same room.
     *
     * This ensures the engine sees historical events before
     * newer turns.
     */

    saveRequests(room, frame);
    observedRoom = room;

    if (lastForwardedFrame.get(room) === frame) return;

    lastForwardedFrame.set(room, frame);

    const pending = queue.filter(item => item.room !== room);

    queue.length = 0;
    queue.push({room, frame, recovered: true}, ...pending);

    flush();
  }

  window.addEventListener("message", event => {
    if (event.source !== window) return;

    const m = event.data;

    if (
      m?.source !== "fpa-page-bridge" ||
      m.token !== TOKEN
    ) {
      return;
    }

    if (m.type === "protocol") {
      receiveProtocol(m.data);
    } else if (m.type === "history") {
      receiveRecoveredHistory(m);
    } else if (m.type === "action-result") {
      if (!m.ok) {
        if (pendingActionKey) sentActions.delete(pendingActionKey);
        if (executeButton) executeButton.disabled = false;
        setStatus(m.error || "Could not send action");
      } else {
        setStatus("Action sent to Showdown");
      }
      pendingActionKey = null;
    }
  });

  function installReceiveHook() {
    const code = `(() => {
      const token = ${JSON.stringify(TOKEN)};
      const marker = Symbol.for("foul-play-advisor.receive-hook");

      const recovered = new Map();

      function send(type, data) {
        window.postMessage({
          source: "fpa-page-bridge",
          token,
          type,
          ...data
        }, "*");
      }

      function sendAction(message) {
        const app = window.app;
        const room = message.room;
        const command = message.command;
        const rqid = message.rqid;

        if (
          !app ||
          typeof app.send !== "function" ||
          typeof room !== "string" ||
          !room.startsWith("battle-") ||
          typeof command !== "string" ||
          !/^\\/(?:choose|switch|team)\\b/.test(command) ||
          rqid === undefined ||
          rqid === null
        ) {
          send("action-result", {
            ok: false,
            error: "Showdown is not ready to send this action",
            actionKey: message.actionKey
          });
          return;
        }

        try {
          app.send(command + "|" + rqid, room);
          send("action-result", {ok: true, actionKey: message.actionKey});
        } catch (error) {
          send("action-result", {
            ok: false,
            error: "Could not send action: " + error.message,
            actionKey: message.actionKey
          });
        }
      }

      window.addEventListener("message", event => {
        const message = event.data;
        if (
          event.source === window &&
          message?.source === "fpa-userscript" &&
          message.token === token &&
          message.type === "action"
        ) {
          sendAction(message);
        }
      });

      function getBattleRooms() {
        const app = window.app;

        if (!app || !app.rooms) return [];

        return Object.entries(app.rooms)
          .filter(([id, room]) =>
            id.startsWith("battle-") && room
          );
      }

      function extractHistory(room) {
        if (!room) return "";

        const battle = room.battle;

        if (!battle) return "";

        /*
         * Pokémon Showdown maintains battle logs internally.
         * Different client versions can expose the history
         * through different properties.
         */

        const candidates = [
          battle.log,
          room.battleLog?.log,
          room.battleLog?.lines
        ];

        let history = "";

        for (const candidate of candidates) {
          if (!candidate) continue;

          if (Array.isArray(candidate)) {
            const lines = candidate.filter(
              line => typeof line === "string"
            );

            if (
              lines.length &&
              lines.some(line =>
                line.startsWith("|turn|") ||
                line.startsWith("|player|") ||
                line.startsWith("|switch|")
              )
            ) {
              history = lines.join("\\n");
              break;
            }
          }

          if (typeof candidate === "string") {
            if (
              candidate.includes("|turn|") ||
              candidate.includes("|player|")
            ) {
              history = candidate;
              break;
            }
          }
        }

        if (!history) return "";

        const request = [
          room.request,
          room.currentRequest,
          battle.request
        ].find(value =>
          value &&
          typeof value === "object" &&
          value.rqid !== undefined
        );

        if (request) {
          const requestLine = "|request|" + JSON.stringify(request);
          const lines = history.replace(/\\r/g, "").split("\\n");
          const existingRequest = lines.find(line =>
            line.startsWith("|request|")
          );
          if (existingRequest !== requestLine) {
            history = lines
              .filter(line => !line.startsWith("|request|"))
              .concat(requestLine)
              .join("\\n");
          }
        }

        return history;
      }

      function recoverRoom(id, room) {
        const history = extractHistory(room);

        if (!history) return;

        if (recovered.get(id) === history) return;

        recovered.set(id, history);

        send("history", {
          room: id,
          history
        });
      }

      function recoverBattles() {
        try {
          for (const [id, room] of getBattleRooms()) {
            recoverRoom(id, room);
          }
        } catch (e) {
          console.warn("[FPA] Recovery failed:", e);
        }
      }

      function install() {
        const app = window.app;

        if (
          !app ||
          typeof app.receive !== "function" ||
          app.receive[marker]
        ) {
          return;
        }

        const original = app.receive;

        function wrappedReceive(...args) {
          try {
            for (const arg of args) {
              if (
                typeof arg === "string" &&
                /(?:^|\\n)>battle-/.test(arg)
              ) {
                send("protocol", {data: arg});
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

      /*
       * Keep checking for new rooms and for the client
       * replacing its receive method.
       */

      setInterval(() => {
        install();
        recoverBattles();
      }, 500);

      /*
       * Initial recovery once Showdown has initialized.
       */

      setTimeout(recoverBattles, 1000);
      setTimeout(recoverBattles, 3000);
    })();`;

    const script = document.createElement("script");
    script.textContent = code;

    (
      document.documentElement ||
      document.head ||
      document
    ).appendChild(script);

    script.remove();
  }

  function visibleRoom() {
    const match = (
      location.hash + " " + location.pathname
    ).match(/battle-[a-z0-9-]+/i);

    return match ? match[0] : observedRoom;
  }

  function prettyMove(id, data) {
    const move = data?.active?.[0]?.moves?.find(m =>
      m.id === id ||
      m.move?.toLowerCase().replace(/[^a-z0-9]/g, "") === id
    );

    if (move?.move) return move.move;

    return id
      .replace(/([a-z])([A-Z])/g, "$1 $2")
      .replace(/\b\w/g, c => c.toUpperCase());
  }

  function readable(command, room) {
    const data = requests.get(room);
    const raw = String(command || "").trim();
    const base = raw.replace(/\|\d+$/, "");

    const sw = base.match(
      /^\/(?:choose\s+)?switch\s+(\d+)/i
    );

    if (sw) {
      const pokemon =
        data?.side?.pokemon?.[Number(sw[1]) - 1];

      const name =
        pokemon?.details?.split(",")[0] ||
        pokemon?.ident?.split(": ")[1];

      return name
        ? "Switch to " + name
        : "Switch to slot " + sw[1];
    }

    const mv = base.match(
      /^\/(?:choose\s+)?move\s+(.+)$/i
    );

    if (mv) {
      const tokens = mv[1].trim().split(/\s+/);
      const id = tokens.shift();

      const idx = Number(id);

      const actualId =
        Number.isInteger(idx) && idx > 0
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
      const order = base.slice(6).split("|")[0].trim();
      const leadSlot = Number(order[0]);
      const lead = data?.side?.pokemon?.[leadSlot - 1];
      const leadName =
        lead?.details?.split(",")[0] ||
        lead?.ident?.split(": ")[1];

      return leadName
        ? `Lead: ${leadName} (team order ${order})`
        : `Lead: slot ${order[0]} (team order ${order})`;
    }

    return base;
  }

  function formatCandidate(candidate, room) {
    const text = readable(candidate, room);
    return text === candidate
      ? text.replace(/\b[a-z]/g, letter => letter.toUpperCase())
      : text;
  }

  function renderList(container, entries, formatEntry) {
    container.replaceChildren();

    if (!entries.length) {
      const empty = document.createElement("div");
      empty.textContent = "No search data available";
      empty.style.color = "#999";
      container.appendChild(empty);
      return;
    }

    for (const [index, entry] of entries.entries()) {
      const row = document.createElement("div");
      row.textContent = formatEntry(entry, index);
      row.style.cssText =
        "padding:2px 0;overflow-wrap:anywhere;color:#ddd;";
      container.appendChild(row);
    }
  }

  function renderOpponentTeam(entries, room) {
    opponentTeam.replaceChildren();
    if (!entries.length) {
      const empty = document.createElement("div");
      empty.textContent = "No team model available yet";
      empty.style.cssText = "font-size:11px;color:#999;padding:4px 0;";
      opponentTeam.appendChild(empty);
      return;
    }

    for (const pokemon of entries) {
      const card = document.createElement("div");
      card.style.cssText =
        "padding:7px;margin:5px 0;background:#303030;" +
        "border:1px solid #444;border-radius:5px;";

      const header = document.createElement("div");
      header.style.cssText =
        "display:flex;justify-content:space-between;align-items:center;" +
        "font-weight:bold;";
      const name = document.createElement("span");
      name.textContent = formatCandidate(pokemon.pokemon, room);
      const percent = Math.max(
        0,
        Math.min(100, Number(pokemon.probability ?? (currentModel ? 1 : 0)) * 100)
      );
      const likelihood = document.createElement("span");
      likelihood.textContent = currentModel
        ? "Assumed"
        : `${Math.round(percent)}% likely`;
      likelihood.style.cssText =
        "font-size:10px;color:#bbb;font-weight:normal;";
      header.append(name, likelihood);
      card.appendChild(header);

      const meter = document.createElement("div");
      meter.style.cssText =
        "height:3px;margin:5px 0 7px;background:#171717;border-radius:2px;";
      const fill = document.createElement("div");
      fill.style.cssText =
        `width:${percent}%;height:100%;background:#64a7dc;border-radius:2px;`;
      meter.appendChild(fill);
      card.appendChild(meter);

      const details = document.createElement("div");
      details.style.cssText = "font-size:10px;color:#ccc;line-height:1.5;";
      const traits = [];
      if (pokemon.known) traits.push("Seen in battle");
      else if (currentModel) traits.push("Your assumption");
      if (pokemon.ability) {
        const likelihoodText = Number.isFinite(
          Number(pokemon.ability_probability)
        )
          ? ` · ${Math.round(Number(pokemon.ability_probability) * 100)}%`
          : "";
        traits.push(`Ability: ${pokemon.ability}${likelihoodText}`);
      } else if (currentModel) {
        traits.push("Ability: engine sample");
      }
      if (pokemon.item) {
        const likelihoodText = Number.isFinite(Number(pokemon.item_probability))
          ? ` · ${Math.round(Number(pokemon.item_probability) * 100)}%`
          : "";
        traits.push(`Item: ${pokemon.item}${likelihoodText}`);
      } else if (currentModel) {
        traits.push("Item: engine sample");
      }
      if (traits.length) {
        const traitLine = document.createElement("div");
        traitLine.textContent = traits.join("  |  ");
        details.appendChild(traitLine);
      }

      if (Array.isArray(pokemon.moves) && pokemon.moves.length) {
        const moveLine = document.createElement("div");
        moveLine.style.cssText =
          "display:flex;flex-wrap:wrap;gap:3px;margin-top:4px;";
        for (const move of pokemon.moves.slice(0, 4)) {
          const moveName = typeof move === "string" ? move : move.move;
          const moveTag = document.createElement("span");
          const chance = typeof move === "string"
            ? ""
            : ` ${Math.round(Number(move.probability) * 100)}%`;
          moveTag.textContent = formatCandidate(moveName, room) + chance;
          moveTag.style.cssText =
            "padding:2px 4px;background:#424242;border-radius:3px;";
          moveLine.appendChild(moveTag);
        }
        details.appendChild(moveLine);
      } else if (currentModel) {
        const moveNote = document.createElement("div");
        moveNote.textContent = "Moves: engine sample";
        details.appendChild(moveNote);
      }

      card.appendChild(details);
      opponentTeam.appendChild(card);
    }
  }

  function sendChosenAction() {
    const recommendation = latestRecommendation;
    if (
      !recommendation ||
      recommendation.room !== activeRoom ||
      recommendation.rqid === undefined ||
      recommendation.rqid === null
    ) {
      setStatus("No current action to send");
      return;
    }

    const key = `${recommendation.room}:${recommendation.rqid}`;
    if (sentActions.has(key)) return;
    sentActions.add(key);
    if (sentActions.size > 100) {
      sentActions.delete(sentActions.values().next().value);
    }

    executeButton.disabled = true;
    pendingActionKey = key;
    window.postMessage({
      source: "fpa-userscript",
      token: TOKEN,
      type: "action",
      room: recommendation.room,
      command: recommendation.command,
      rqid: recommendation.rqid,
      actionKey: key
    }, "*");
  }

  function scheduleAutoAction() {
    clearTimeout(autoTimer);
    autoTimer = null;

    if (!autoToggle?.checked || !latestRecommendation) return;
    const key = `${latestRecommendation.room}:${latestRecommendation.rqid}`;
    if (sentActions.has(key)) return;

    const delay = 4000 + (Math.random() * 2 - 1) * 1000;
    autoTimer = setTimeout(() => {
      autoTimer = null;
      if (
        autoToggle.checked &&
        latestRecommendation &&
        `${latestRecommendation.room}:${latestRecommendation.rqid}` === key
      ) {
        sendChosenAction();
      }
    }, delay);
  }

  function renderTeamEditor() {
    teamEditor.replaceChildren();
    for (const pokemon of modelDraft) {
      const row = document.createElement("div");
      row.style.cssText =
        "display:grid;grid-template-columns:1fr auto;gap:4px;" +
        "margin:6px 0;padding:6px;background:#303030;border-radius:4px;";

      const fields = document.createElement("div");
      fields.style.cssText =
        "display:grid;grid-template-columns:1fr 1fr;gap:4px;";

      const addInput = (field, value, placeholder) => {
        const input = document.createElement("input");
        input.type = "text";
        input.dataset.field = field;
        input.value = value || "";
        input.placeholder = placeholder;
        input.style.cssText =
          "box-sizing:border-box;width:100%;min-width:0;padding:4px;" +
          "background:#1c1c1c;color:#eee;border:1px solid #555;border-radius:3px;";
        input.addEventListener("input", () => {
          pokemon[field] = field === "moves"
            ? input.value.split(",").map(move => move.trim()).filter(Boolean)
            : input.value;
        });
        fields.appendChild(input);
      };

      addInput("pokemon", pokemon.pokemon, "Pokemon (e.g. Garchomp)");
      addInput(
        "moves",
        Array.isArray(pokemon.moves)
          ? pokemon.moves.map(move =>
            typeof move === "string" ? move : move.move
          ).join(", ")
          : "",
        "Moves, comma-separated"
      );
      addInput("ability", pokemon.ability, "Ability (optional)");
      addInput("item", pokemon.item, "Item (optional)");

      const remove = document.createElement("button");
      remove.type = "button";
      remove.textContent = "Remove";
      remove.style.cssText = "align-self:start;";
      remove.addEventListener("click", () => {
        modelDraft = modelDraft.filter(entry => entry !== pokemon);
        renderTeamEditor();
      });

      row.append(fields, remove);
      teamEditor.appendChild(row);
    }

    const controls = document.createElement("div");
    controls.style.cssText =
      "display:flex;gap:6px;margin-top:6px;";
    const add = document.createElement("button");
    add.type = "button";
    add.textContent = "Add Pokemon";
    add.addEventListener("click", () => {
      if (modelDraft.length >= 6) {
        setStatus("The model can contain at most six Pokemon");
        return;
      }
      modelDraft.push({pokemon: "", moves: [], ability: "", item: ""});
      renderTeamEditor();
    });

    const save = document.createElement("button");
    save.type = "button";
    save.textContent = "Save model";
    save.addEventListener("click", () => {
      const rows = [...teamEditor.children].filter(child =>
        child.querySelector("[data-field='pokemon']")
      );
      const model = rows.map(row => {
        const value = field =>
          row.querySelector(`[data-field='${field}']`).value.trim();
        return {
          pokemon: value("pokemon"),
          moves: value("moves")
            .split(",")
            .map(move => move.trim())
            .filter(Boolean),
          ability: value("ability"),
          item: value("item")
        };
      }).filter(entry => entry.pokemon);

      if (model.length > 6) {
        setStatus("The model can contain at most six Pokemon");
        return;
      }
      if (model.some(pokemon => pokemon.moves.length > 4)) {
        setStatus("Each Pokemon can have at most four moves");
        return;
      }

      api(
        "POST",
        JSON.stringify({room: activeRoom, model}),
        error => {
          if (error) {
            setStatus(`Could not save model: ${error.message}`);
            return;
          }
          currentModel = model;
          editTeamButton.textContent = "Edit team model";
          teamEditor.hidden = true;
          editTeamButton.dataset.editing = "false";
          setStatus("Model saved; it will affect the next search");
          renderAnalysis(currentAnalysis, activeRoom);
        },
        {
          "Content-Type": "application/json",
          "X-FPA-Model": "1"
        }
      );
    });
    controls.append(add, save);
    teamEditor.appendChild(controls);
  }

  function renderAnalysis(analysis, room) {
    if (!evaluation) return;
    currentAnalysis = analysis;

    if (!analysis) {
      rationale.textContent = "";
      evaluation.textContent = "Position estimate unavailable";
      evaluationMarker.style.left = "50%";
      evaluationBar.style.opacity = "0.35";
      renderList(choices, [], () => "");
      renderList(opponentMoves, [], () => "");
      renderList(opponentTeam, [], () => "");
      return;
    }

    rationale.textContent = analysis.reason || "";
    const score = Number(analysis.evaluation);
    if (
      analysis.evaluation !== null &&
      analysis.evaluation !== undefined &&
      Number.isFinite(score)
    ) {
      const boundedScore = Math.max(-1, Math.min(1, score));
      const position = Math.round((50 + boundedScore * 50) * 10) / 10;
      evaluationMarker.style.left = position + "%";
      evaluationBar.style.opacity = "1";
      const leader = score > 0.02
        ? "You"
        : score < -0.02
        ? "Opponent"
        : "Even";
      const advantage = Math.abs(score).toFixed(2);
      evaluation.textContent =
        leader === "Even"
          ? `Position estimate: even (${score >= 0 ? "+" : ""}${score.toFixed(2)})`
          : `Position estimate: ${leader} +${advantage}`;
    } else {
      evaluation.textContent = "Position estimate unavailable";
      evaluationMarker.style.left = "50%";
      evaluationBar.style.opacity = "0.35";
    }

    const rankedChoices = Array.isArray(analysis.choices)
      ? analysis.choices
      : [];
    renderList(choices, rankedChoices, (choice, index) => {
      const scoreText = choice.score !== null &&
        choice.score !== undefined &&
        Number.isFinite(Number(choice.score))
        ? ` · eval ${Number(choice.score) >= 0 ? "+" : ""}${Number(choice.score).toFixed(2)}`
        : "";
      const selected = index + 1 === analysis.selected_rank
        ? " (recommended)"
        : "";
      return `#${index + 1} ${formatCandidate(choice.move, room)} · ` +
        `${(Number(choice.probability) * 100).toFixed(1)}%${scoreText}${selected}`;
    });

    renderList(
      opponentMoves,
      Array.isArray(analysis.opponent_moves) ? analysis.opponent_moves : [],
      choice =>
        `${formatCandidate(choice.move, room)} · ` +
        `${(Number(choice.probability) * 100).toFixed(1)}%`
    );

    const teamEntries = currentModel || (
      Array.isArray(analysis.opponent_team) ? analysis.opponent_team : []
    );
    renderOpponentTeam(teamEntries, room);
  }

  function poll() {
    const room = visibleRoom();

    if (room !== activeRoom) {
      activeRoom = room;
      lastSeq = -1;
      latestRecommendation = null;
      currentModel = null;
      if (executeButton) executeButton.disabled = true;
      clearTimeout(autoTimer);
      autoTimer = null;

      if (roomLabel) {
        roomLabel.textContent =
          room || "No active battle";
      }

      if (result) result.textContent = "";
      renderAnalysis(null, room);

      setStatus(
        room
          ? "Waiting for analysis"
          : "Waiting for battle"
      );
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

        if (roomLabel) {
          roomLabel.textContent = activeRoom;
        }
      }

      if (
        data.room === activeRoom &&
        currentModel === null &&
        Array.isArray(data.opponent_model)
      ) {
        currentModel = data.opponent_model;
      }

      if (
        data.room !== activeRoom ||
        data.seq === lastSeq
      ) {
        return;
      }

      lastSeq = data.seq;

      if (data.status === "ready") {
        latestRecommendation = {
          room: activeRoom,
          command: String(data.text || "").replace(/\|\d+$/, ""),
          rqid: data.rqid
        };
        if (executeButton) {
          executeButton.disabled =
            data.rqid === undefined ||
            data.rqid === null ||
            sentActions.has(`${activeRoom}:${data.rqid}`);
        }
        if (result) {
          result.textContent =
            readable(data.text, activeRoom);
        }

        renderAnalysis(data.analysis, activeRoom);
        setStatus("Recommendation");
        scheduleAutoAction();

      } else if (data.status === "thinking") {
        latestRecommendation = null;
        if (executeButton) executeButton.disabled = true;
        clearTimeout(autoTimer);
        autoTimer = null;
        if (result) result.textContent = "";
        renderAnalysis(null, activeRoom);
        setStatus("Thinking...");

      } else if (data.status === "waiting") {
        latestRecommendation = null;
        if (executeButton) executeButton.disabled = true;
        clearTimeout(autoTimer);
        autoTimer = null;
        if (result) result.textContent = "";
        setStatus("Waiting for opponent...");

      } else if (data.status === "finished") {
        latestRecommendation = null;
        if (executeButton) executeButton.disabled = true;
        clearTimeout(autoTimer);
        autoTimer = null;
        if (result) result.textContent = "";
        renderAnalysis(null, activeRoom);
        setStatus("Battle finished");

      } else if (data.status === "error") {
        latestRecommendation = null;
        if (executeButton) executeButton.disabled = true;
        clearTimeout(autoTimer);
        autoTimer = null;
        if (result) {
          result.textContent =
            data.text || "Check Python terminal";
        }

        renderAnalysis(null, activeRoom);
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
      width:310px;
      max-height:75vh;
      overflow-y:auto;
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
      "font-weight:bold;cursor:move;" +
      "user-select:none;margin-bottom:8px;";

    roomLabel = document.createElement("div");

    roomLabel.style.cssText =
      "font-size:11px;color:#aaa;" +
      "overflow-wrap:anywhere;margin-bottom:8px;";

    status = document.createElement("div");

    status.style.cssText =
      "font-size:12px;color:#bbb;margin-bottom:8px;";

    result = document.createElement("div");

    result.style.cssText =
      "font-size:15px;font-weight:bold;" +
      "overflow-wrap:anywhere;";

    const actionControls = document.createElement("div");
    actionControls.style.cssText =
      "display:flex;align-items:center;gap:8px;margin:8px 0;";

    executeButton = document.createElement("button");
    executeButton.type = "button";
    executeButton.textContent = "Use recommended action";
    executeButton.disabled = true;
    executeButton.addEventListener("click", sendChosenAction);

    const autoLabel = document.createElement("label");
    autoLabel.style.cssText =
      "display:flex;align-items:center;gap:4px;font-size:11px;color:#ddd;";
    autoToggle = document.createElement("input");
    autoToggle.type = "checkbox";
    autoToggle.addEventListener("change", () => {
      if (autoToggle.checked) {
        scheduleAutoAction();
      } else {
        clearTimeout(autoTimer);
        autoTimer = null;
      }
    });
    autoLabel.append(autoToggle, "Auto (3-5 sec)");
    actionControls.append(executeButton, autoLabel);

    rationale = document.createElement("div");
    rationale.style.cssText =
      "font-size:12px;color:#ccc;margin:6px 0;overflow-wrap:anywhere;";

    evaluation = document.createElement("div");
    evaluation.style.cssText =
      "font-size:12px;color:#ddd;margin:8px 0 5px;";

    evaluationBar = document.createElement("div");
    evaluationBar.style.cssText =
      "height:10px;position:relative;border-radius:5px;" +
      "background:linear-gradient(90deg,#bd4a4a 0%,#777 50%,#4ea85c 100%);" +
      "margin-bottom:10px;";

    evaluationMarker = document.createElement("div");
    evaluationMarker.style.cssText =
      "position:absolute;top:-3px;left:50%;height:16px;width:3px;" +
      "background:#fff;box-shadow:0 0 3px #000;transform:translateX(-50%);";
    evaluationBar.appendChild(evaluationMarker);

    choices = document.createElement("div");
    opponentMoves = document.createElement("div");
    opponentTeam = document.createElement("div");

    panel.append(
      heading,
      roomLabel,
      status,
      result,
      actionControls,
      rationale,
      evaluation,
      evaluationBar
    );
    const rankingHeading = document.createElement("div");
    rankingHeading.textContent = "MOVE RANKINGS";
    rankingHeading.style.cssText =
      "font-size:12px;font-weight:bold;color:#aaa;margin:8px 0 3px;";
    const opponentMovesHeading = document.createElement("div");
    opponentMovesHeading.textContent = "LIKELY OPPONENT ACTIONS";
    opponentMovesHeading.style.cssText =
      "font-size:12px;font-weight:bold;color:#aaa;margin:8px 0 3px;";
    const opponentTeamHeading = document.createElement("div");
    opponentTeamHeading.textContent = "OPPONENT TEAM MODEL";
    opponentTeamHeading.style.cssText =
      "font-size:12px;font-weight:bold;color:#aaa;margin:8px 0 3px;";
    const modelNote = document.createElement("div");
    modelNote.textContent =
      "Guesses are sampled estimates. Edit to influence future searches.";
    modelNote.style.cssText =
      "font-size:10px;color:#999;margin-bottom:4px;";
    editTeamButton = document.createElement("button");
    editTeamButton.type = "button";
    editTeamButton.textContent = "Edit team model";
    editTeamButton.style.cssText = "margin:0 0 6px;";
    editTeamButton.dataset.editing = "false";
    editTeamButton.addEventListener("click", () => {
      if (editTeamButton.dataset.editing === "true") {
        editTeamButton.dataset.editing = "false";
        editTeamButton.textContent = "Edit team model";
        teamEditor.hidden = true;
        return;
      }
      const source = currentModel || currentAnalysis?.opponent_team || [];
      modelDraft = source.slice(0, 6).map(pokemon => ({
        pokemon: pokemon.pokemon || "",
        moves: Array.isArray(pokemon.moves)
          ? pokemon.moves.map(move =>
            typeof move === "string" ? move : move.move
          )
          : [],
        ability: pokemon.ability || "",
        item: pokemon.item || ""
      }));
      renderTeamEditor();
      teamEditor.hidden = false;
      editTeamButton.dataset.editing = "true";
      editTeamButton.textContent = "Cancel editing";
    });
    teamEditor = document.createElement("div");
    teamEditor.hidden = true;
    teamEditor.style.cssText =
      "padding:6px;background:#252525;border-radius:4px;margin-bottom:8px;";
    panel.append(
      rankingHeading,
      choices,
      opponentMovesHeading,
      opponentMoves,
      opponentTeamHeading,
      modelNote,
      opponentTeam,
      editTeamButton,
      teamEditor
    );

    document.body.appendChild(panel);

    let drag = false;
    let x = 0;
    let y = 0;

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

      panel.style.left =
        (e.clientX - x) + "px";

      panel.style.top =
        (e.clientY - y) + "px";
    });

    document.addEventListener("mouseup", () => {
      drag = false;
    });

    poll();
  }

  installReceiveHook();

  const timer = setInterval(() => {
    createPanel();

    if (panel) clearInterval(timer);
  }, 100);

  setInterval(poll, 750);
})();