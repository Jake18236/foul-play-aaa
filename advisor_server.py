import asyncio
import json
import logging
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fp.battle.helpers import normalize_name
from fp.config import FoulPlayConfig
from fp.data import all_move_json, pokedex
from fp.run_battle import pokemon_battle
from fp.opponent_memory import remember_protocol

PORT = 8765
sessions = {}
opponent_models = {}
latest = {
    "room": None,
    "text": "",
    "seq": 0,
    "rqid": None,
    "status": "waiting",
    "analysis": None,
}
configured = False
loop = None
logger = logging.getLogger("advisor")


def format_from_battle_room(room):
    match = re.match(r"^battle-(gen\d+[a-z0-9]*)-\d+(?:-|$)", room)
    if not match:
        raise ValueError(f"Could not determine battle format from room ID: {room}")
    return match.group(1)


def publish(room, text, status, rqid=None, analysis=None):
    latest.update(
        room=room,
        text=text,
        status=status,
        rqid=rqid,
        analysis=analysis,
        seq=latest["seq"] + 1,
    )
    logger.info("%s %s rqid=%s: %s", room, status, rqid, text)


def protocol_frames(body):
    """Extract room frames without treating the entire WebSocket payload as one room."""
    current = None
    lines = []
    for line in body.replace("\r", "").split("\n"):
        if line.startswith(">"):
            if current:
                yield current, lines
            current = line[1:].strip()
            lines = []
        elif current:
            lines.append(line)
    if current:
        yield current, lines


def messages_for_foul_play(room, lines, preserve_order=False):
    """Split startup events, but preserve the order of live protocol frames."""
    if preserve_order:
        yield f">{room}\n" + "\n".join(lines)
        return

    title = next((x for x in lines if x.startswith("|title|")), None)
    if title:
        yield f">{room}\n|init|battle\n{title}"
    for line in lines:
        if line.startswith("|player|"):
            yield f">{room}\n{line}"
    start_at = next(
        (
            i
            for i, x in enumerate(lines)
            if x in {"|start", "|clearpoke", "|teampreview"}
        ),
        None,
    )
    if start_at is not None:
        # Keep all subsequent non-request updates with the start event.
        updates = [x for x in lines[start_at:] if not x.startswith("|request|")]
        yield f">{room}\n" + "\n".join(updates)
    for line in lines:
        if line.startswith("|request|"):
            yield f">{room}\n{line}"
    if start_at is None:
        other = [
            x
            for x in lines
            if x and not (x.startswith(("|title|", "|init|", "|player|", "|request|")))
        ]
        if other:
            yield f">{room}\n" + "\n".join(other)


class FakeClient:
    def __init__(self, room):
        self.room = room
        self.queue = asyncio.Queue()
        self.last_choice = None
        self.analysis = None

    def set_analysis(self, analysis):
        # Keep reproducible engine states on disk without sending large state
        # strings to the userscript on every poll.
        self.analysis = dict(analysis)
        states = self.analysis.pop("search_states", [])
        try:
            trace_logger = logging.getLogger("advisor.decisions")
            if not trace_logger.handlers:
                trace_path = (
                    Path(__file__).resolve().parent / "logs/advisor-decisions.jsonl"
                )
                trace_path.parent.mkdir(parents=True, exist_ok=True)
                handler = RotatingFileHandler(
                    trace_path, maxBytes=5_000_000, backupCount=2
                )
                handler.setFormatter(logging.Formatter("%(message)s"))
                trace_logger.addHandler(handler)
                trace_logger.setLevel(logging.INFO)
                trace_logger.propagate = False
            trace_logger.info(
                json.dumps(
                    {
                        "room": self.room,
                        "analysis": self.analysis,
                        "search_states": states,
                    }
                )
            )
        except OSError:
            logger.warning("Could not save advisor decision trace", exc_info=True)
        session = sessions.get(self.room)
        if session is not None:
            session.remember()

    def get_opponent_model(self):
        return opponent_models.get(self.room)

    async def receive_message(self):
        return await self.queue.get()

    async def send_message(self, room, messages):
        if isinstance(messages, (list, tuple)):
            parts = [str(m) for m in messages]
        else:
            parts = [str(messages)]
        if not parts or parts[0] in ("hf", "gg") or parts[0].startswith("/timer"):
            return
        command = parts[0]
        rqid = parts[1] if len(parts) > 1 else None
        if rqid is None:
            match = re.search(r"\|(\d+)$", command)
            if match:
                rqid = match.group(1)
        # Avoid publishing identical suggestions for the same request,
        # without suppressing all subsequent turns.
        key = (rqid, command)
        if key == self.last_choice:
            return
        self.last_choice = key
        publish(self.room, command, "ready", rqid, self.analysis)
        print("ADVISOR RECOMMENDS:", command, flush=True)

    def __getattr__(self, name):
        async def noop(*args, **kwargs):
            return None

        return noop


def configure_once(name):
    global configured
    if configured:
        return
    sys.argv = [
        "run.py",
        "--websocket-uri",
        "ws://localhost:8000/showdown/websocket",
        "--ps-username",
        name,
        "--bot-mode",
        "accept_challenge",
        "--pokemon-format",
        "gen9randombattle",
        "--search-time-ms",
        "500",
        "--save-replay",
        "never",
    ]
    FoulPlayConfig.configure()
    configured = True
    logger.info("Configured as %s", name)


class Session:
    def __init__(self, room):
        self.room = room
        self.client = FakeClient(room)
        self.buffer = []
        self.task = None
        self.seen_title = False
        self.seen_start = False
        self.seen_team_preview = False
        self.seen_request = set()
        self.finished = False
        self.protocol_history = []
        self.memory_request = None

    def remember(self):
        if self.memory_request:
            remember_protocol(
                self.room,
                format_from_battle_room(self.room),
                self.protocol_history + [self.memory_request],
            )

    def feed(self, lines, recovered=False):
        if self.finished:
            return

        if recovered and self.task is not None:
            recovered_history = [
                line for line in lines if line and not line.startswith("|request|")
            ]
            requests = [line for line in lines if line.startswith("|request|")]
            known = self.protocol_history

            if known and recovered_history[: len(known)] == known:
                recovered_history = recovered_history[len(known) :]
            elif known and known[: len(recovered_history)] == recovered_history:
                recovered_history = []
            elif known and recovered_history:
                overlap = min(len(known), len(recovered_history))
                while overlap and known[-overlap:] != recovered_history[:overlap]:
                    overlap -= 1
                if overlap:
                    recovered_history = recovered_history[overlap:]
                else:
                    logger.warning(
                        "Could not align recovered history for %s; skipping replay",
                        self.room,
                    )
                    recovered_history = []

            lines = recovered_history + requests[-1:]
            if not lines:
                return

        self.protocol_history.extend(
            line for line in lines if line and not line.startswith("|request|")
        )
        for line in lines:
            if line.startswith("|request|") and '"side"' in line:
                self.memory_request = line

        # Never mistake a chat message mentioning '|win|' for battle completion.
        if any(x.startswith(("|win|", "|tie|")) for x in lines):
            self.remember()
            self.finished = True
            publish(self.room, "Game over", "finished")
            if self.task:
                self.task.cancel()
            sessions.pop(self.room, None)
            return

        # Deduplicate individual startup events, never their entire frame:
        # that frame may also contain the next actionable request.
        filtered = []
        for line in lines:
            flag = (
                "seen_title" if line.startswith("|title|") else
                "seen_start" if line == "|start" else
                "seen_team_preview" if line.startswith("|teampreview") else None
            )
            if flag:
                if getattr(self, flag):
                    continue
                setattr(self, flag, True)
            filtered.append(line)

        for msg in messages_for_foul_play(
            self.room, filtered, preserve_order=self.task is not None
        ):
            if "\n|request|" in msg:
                try:
                    request_line = next(
                        line for line in msg.splitlines() if line.startswith("|request|")
                    )
                    data = json.loads(request_line[9:])
                    rqid = data.get("rqid")
                    if rqid is not None:
                        if rqid in self.seen_request:
                            continue
                        self.seen_request.add(rqid)
                        waiting = data.get("wait") is True
                        publish(
                            self.room,
                            "Waiting for opponent" if waiting else "Thinking",
                            "waiting" if waiting else "thinking",
                            rqid,
                        )
                except (ValueError, TypeError):
                    logger.warning("Invalid request JSON for %s", self.room)
            self.buffer.append(msg)

        if self.task is None:
            # The startup parser requires title, player, start and request.
            joined = "\n".join(self.buffer)
            match = re.search(r'"side"\s*:\s*\{[^{}]*"name"\s*:\s*"([^"]+)"', joined)
            if not (
                match
                and self.seen_title
                and (self.seen_start or self.seen_team_preview)
                and "|player|" in joined
            ):
                return
            configure_once(match.group(1))
            self.task = asyncio.create_task(self.run())

        for msg in self.buffer:
            self.client.queue.put_nowait(msg)
        self.buffer.clear()

    async def run(self):
        fmt = format_from_battle_room(self.room)
        logger.info("Starting battle %s format=%s", self.room, fmt)
        try:
            await pokemon_battle(
                self.client,
                fmt,
                {},
                analysis_callback=self.client.set_analysis,
                opponent_model_callback=self.client.get_opponent_model,
            )
            if not self.finished:
                publish(self.room, "Game over", "finished")
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Advisor failed for %s", self.room)
            publish(self.room, "Advisor error; check terminal", "error")
        finally:
            if sessions.get(self.room) is self:
                sessions.pop(self.room, None)


def handle(body, recovered=False):
    for room, lines in protocol_frames(body):
        if not room.startswith("battle-"):
            continue
        session = sessions.get(room)
        if session is None:
            session = sessions[room] = Session(room)
        session.feed(lines, recovered=recovered)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        size = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(size).decode("utf-8", "replace")
        if self.headers.get("X-FPA-Model") == "1":
            try:
                payload = json.loads(body)
                room = payload.get("room")
                model = payload.get("model")
                if not isinstance(room, str) or not room.startswith("battle-"):
                    raise ValueError("Invalid battle room")
                if not isinstance(model, list) or len(model) > 6:
                    raise ValueError("Opponent model must contain at most six Pokemon")
                seen = set()
                for entry in model:
                    if not isinstance(entry, dict):
                        raise ValueError("Each opponent entry must be an object")
                    pokemon = normalize_name(entry.get("pokemon", ""))
                    if pokemon not in pokedex:
                        raise ValueError(f"Unknown Pokemon: {entry.get('pokemon', '')}")
                    if pokemon in seen:
                        raise ValueError(
                            f"Duplicate Pokemon: {entry.get('pokemon', '')}"
                        )
                    seen.add(pokemon)
                    moves = entry.get("moves", [])
                    if not isinstance(moves, list) or len(moves) > 4:
                        raise ValueError("Each Pokemon can have at most four moves")
                    if any(
                        not isinstance(entry.get(field, ""), str)
                        for field in ("ability", "item")
                    ):
                        raise ValueError("Ability and item must be text")
                    for move in moves:
                        normalized_move = (
                            normalize_name(move) if isinstance(move, str) else ""
                        )
                        if (
                            normalized_move not in all_move_json
                            and not normalized_move.startswith("hiddenpower")
                        ):
                            raise ValueError(f"Unknown move: {move}")
                opponent_models[room] = model
            except (json.JSONDecodeError, ValueError, AttributeError) as error:
                self.send_error(400, str(error))
                return
            self.send_response(204)
            self.end_headers()
            return

        recovered = self.headers.get("X-FPA-Recovery") == "1"
        loop.call_soon_threadsafe(handle, body, recovered)
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        payload_data = dict(latest)
        if payload_data["room"]:
            payload_data["opponent_model"] = opponent_models.get(payload_data["room"])
        payload = json.dumps(payload_data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


def main():
    global loop
    logging.basicConfig(level=logging.INFO)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"Advisor listening on http://127.0.0.1:{PORT}", flush=True)
    loop.run_forever()


if __name__ == "__main__":
    main()
