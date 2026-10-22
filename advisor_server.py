import asyncio
import json
import logging
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fp.config import FoulPlayConfig
from fp.run_battle import pokemon_battle

PORT = 8765
sessions = {}
latest = {"room": None, "text": "", "seq": 0, "rqid": None, "status": "waiting"}
configured = False
loop = None
logger = logging.getLogger("advisor")


def publish(room, text, status, rqid=None):
    latest.update(room=room, text=text, status=status, rqid=rqid, seq=latest["seq"] + 1)
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


def messages_for_foul_play(room, lines):
    """Foul Play's startup expects separate title, player, start, request messages."""
    title = next((x for x in lines if x.startswith("|title|")), None)
    if title:
        yield f">{room}\n|init|battle\n{title}"
    for line in lines:
        if line.startswith("|player|"):
            yield f">{room}\n{line}"
    start_at = next((i for i, x in enumerate(lines) if x == "|start"), None)
    if start_at is not None:
        # Keep all subsequent non-request updates with the start event.
        updates = [x for x in lines[start_at:] if not x.startswith("|request|")]
        yield f">{room}\n" + "\n".join(updates)
    for line in lines:
        if line.startswith("|request|"):
            yield f">{room}\n{line}"
    if start_at is None:
        other = [x for x in lines if x and not (
            x.startswith(("|title|", "|init|", "|player|", "|request|"))
        )]
        if other:
            yield f">{room}\n" + "\n".join(other)


class FakeClient:
    def __init__(self, room):
        self.room = room
        self.queue = asyncio.Queue()
        self.last_choice = None

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
        publish(self.room, command, "ready", rqid)
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
        "run.py", "--websocket-uri", "ws://localhost:8000/showdown/websocket",
        "--ps-username", name, "--bot-mode", "accept_challenge",
        "--pokemon-format", "gen9randombattle",
        "--search-time-ms", "1000", "--save-replay", "never",
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
        self.seen_request = set()
        self.finished = False

    def feed(self, lines):
        if self.finished:
            return
        # Never mistake a chat message mentioning '|win|' for battle completion.
        if any(x.startswith(("|win|", "|tie|")) for x in lines):
            self.finished = True
            publish(self.room, "Game over", "finished")
            if self.task:
                self.task.cancel()
            sessions.pop(self.room, None)
            return

        for msg in messages_for_foul_play(self.room, lines):
            if "|title|" in msg:
                if self.seen_title:
                    continue
                self.seen_title = True
            if "\n|start" in msg:
                if self.seen_start:
                    continue
                self.seen_start = True
            if "\n|request|" in msg:
                try:
                    data = json.loads(msg.split("|request|", 1)[1].strip())
                    rqid = data.get("rqid")
                    if rqid is not None:
                        if rqid in self.seen_request:
                            continue
                        self.seen_request.add(rqid)
                        publish(self.room, "Thinking", "thinking", rqid)
                except (ValueError, TypeError):
                    logger.warning("Invalid request JSON for %s", self.room)
            self.buffer.append(msg)

        if self.task is None:
            # The startup parser requires title, player, start and request.
            joined = "\n".join(self.buffer)
            match = re.search(r'"side"\s*:\s*\{[^{}]*"name"\s*:\s*"([^"]+)"', joined)
            if not (match and self.seen_title and self.seen_start and "|player|" in joined):
                return
            configure_once(match.group(1))
            self.task = asyncio.create_task(self.run())

        for msg in self.buffer:
            self.client.queue.put_nowait(msg)
        self.buffer.clear()

    async def run(self):
        fmt = self.room.removeprefix("battle-").rsplit("-", 1)[0]
        logger.info("Starting battle %s format=%s", self.room, fmt)
        try:
            await pokemon_battle(self.client, fmt, {})
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


def handle(body):
    for room, lines in protocol_frames(body):
        if not room.startswith("battle-"):
            continue
        session = sessions.get(room)
        if session is None:
            session = sessions[room] = Session(room)
        session.feed(lines)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        size = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(size).decode("utf-8", "replace")
        loop.call_soon_threadsafe(handle, body)
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        payload = json.dumps(latest).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
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
