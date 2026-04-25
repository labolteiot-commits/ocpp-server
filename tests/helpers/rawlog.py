"""
Tail incrémental d'un fichier raw_logger (core/raw_logger.py).

Format de ligne :
  "2026-04-22T15:05:08-0400 INFO VIRTUAL-001: send [2,\"<uuid>\",\"Action\",{...}]"
  "2026-04-22T15:05:08-0400 INFO VIRTUAL-001: receive message [3,\"<uuid>\",{...}]"
  "2026-04-22T15:05:08-0400 INFO VIRTUAL-001: receive message [2,\"<uuid>\",\"Action\",{...}]"

OCPP 1.6 message types (1er élément du payload) :
  2 = CALL         [2, messageId, action, payload]
  3 = CALLRESULT   [3, messageId, payload]
  4 = CALLERROR    [4, messageId, errorCode, errorDescription, errorDetails]
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Optional


_LINE_RE = re.compile(
    r"^(?P<ts>\S+)\s+\S+\s+(?P<cp>[^:]+):\s+"
    r"(?P<dir>send|receive message|receive unhandled message)\s+"
    r"(?P<payload>\[.*\])\s*$"
)


@dataclass
class OCPPFrame:
    timestamp: str
    charger_id: str
    direction: str          # "send" | "receive"
    msg_type: int           # 2 (CALL) | 3 (CALLRESULT) | 4 (CALLERROR)
    message_id: str
    action: Optional[str]   # présent sur CALL (type 2) uniquement
    payload: object         # dict|str selon type

    @property
    def is_call(self) -> bool:
        return self.msg_type == 2

    @property
    def is_result(self) -> bool:
        return self.msg_type == 3

    @property
    def is_error(self) -> bool:
        return self.msg_type == 4


def parse_line(line: str) -> Optional[OCPPFrame]:
    m = _LINE_RE.match(line.rstrip("\n"))
    if not m:
        return None
    try:
        arr = json.loads(m.group("payload"))
    except json.JSONDecodeError:
        return None
    if not isinstance(arr, list) or len(arr) < 2:
        return None

    msg_type = int(arr[0])
    message_id = str(arr[1])
    direction = "send" if m.group("dir") == "send" else "receive"

    if msg_type == 2 and len(arr) >= 4:
        action = str(arr[2])
        payload = arr[3]
    elif msg_type == 3 and len(arr) >= 3:
        action = None
        payload = arr[2]
    elif msg_type == 4:
        action = None
        payload = arr[2:] if len(arr) > 2 else None
    else:
        action = None
        payload = None

    return OCPPFrame(
        timestamp=m.group("ts"),
        charger_id=m.group("cp").strip(),
        direction=direction,
        msg_type=msg_type,
        message_id=message_id,
        action=action,
        payload=payload,
    )


class RawLogTail:
    """Ouvre le fichier, se positionne en fin, lit les nouvelles lignes."""

    def __init__(self, path: str):
        self.path = path
        self._fh = None
        self._buffer: list[OCPPFrame] = []

    def open(self):
        # UTF-8 + errors=replace car les logs raw peuvent contenir des bytes
        # bizarres (payload vendor-specific)
        self._fh = open(self.path, "r", encoding="utf-8", errors="replace")
        self._fh.seek(0, 2)  # end of file

    def close(self):
        if self._fh:
            self._fh.close()
            self._fh = None

    def drain(self) -> list[OCPPFrame]:
        """Lit toutes les nouvelles lignes disponibles, parse en OCPPFrame.

        Les lignes non-conformes (format inattendu) sont silencieusement
        ignorées — elles sont rares et relèvent de handlers lib OCPP
        internes ("NotImplemented…") qu'on ne teste pas ici.
        """
        assert self._fh is not None
        new_frames: list[OCPPFrame] = []
        for raw in self._fh:
            frame = parse_line(raw)
            if frame:
                new_frames.append(frame)
        self._buffer.extend(new_frames)
        return new_frames

    async def wait_for(self, *, action: Optional[str] = None,
                       direction: Optional[str] = None,
                       msg_type: Optional[int] = None,
                       timeout: float = 10.0,
                       poll: float = 0.25) -> OCPPFrame:
        """Bloque jusqu'à une frame matchant les critères ou timeout."""
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            # Cherche d'abord dans le buffer existant
            for f in self._buffer:
                if action and f.action != action:
                    continue
                if direction and f.direction != direction:
                    continue
                if msg_type is not None and f.msg_type != msg_type:
                    continue
                self._buffer.remove(f)
                return f
            # Drain nouvelle salve
            self.drain()
            await asyncio.sleep(poll)
        raise TimeoutError(
            f"Pas de frame (action={action}, direction={direction}, "
            f"msg_type={msg_type}) après {timeout}s"
        )
