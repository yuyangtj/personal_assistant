"""The reply as it is being written, a sentence at a time, so it can be spoken early.

Models answer with JSON (``{"reply": "...", "emotion": ...}``). While the JSON streams in,
``ReplyDraft`` pulls the reply string out of it as far as it has arrived, and hands each
newly completed sentence's text so far to ``emit``. Text that is not that JSON reply (a tool
call written as text, for example) is never emitted.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from contextlib import suppress

_REPLY_KEY = re.compile(r'"reply"\s*:\s*"')
#: A sentence ends at . ! ? (or …) followed by a space, a quote or the end of the reply.
_SENTENCE_END = re.compile(r'[.!?…]+["”’)]*(?=\s)')
_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}


def partial_reply(raw: str) -> tuple[str, bool]:
    """The reply string's text as far as it has arrived, and whether it is complete."""
    match = _REPLY_KEY.search(raw)
    if match is None:
        return "", False
    out: list[str] = []
    i = match.end()
    while i < len(raw):
        char = raw[i]
        if char == '"':
            return "".join(out), True
        if char == "\\":
            if i + 1 >= len(raw):
                break  # the escape hasn't fully arrived
            code = raw[i + 1]
            if code == "u":
                digits = raw[i + 2 : i + 6]
                if len(digits) < 4:
                    break
                with suppress(ValueError):
                    out.append(chr(int(digits, 16)))
                i += 6
                continue
            out.append(_ESCAPES.get(code, code))
            i += 2
            continue
        out.append(char)
        i += 1
    return "".join(out), False


class ReplyDraft:
    """Feeds on streamed text; emits the reply up to each sentence that has ended."""

    def __init__(self, emit: Callable[[str], None]):
        self.emit = emit
        self.raw = ""
        self.sent = 0  # how much of the reply has been emitted

    def feed(self, piece: str) -> None:
        self.raw += piece
        if not self.raw.lstrip().startswith("{"):
            return  # not the JSON reply (plain text or a tool call written out)
        reply, done = partial_reply(self.raw)
        if done:
            end = len(reply.rstrip())
        else:
            ends = [m.end() for m in _SENTENCE_END.finditer(reply)]
            end = ends[-1] if ends else 0
        if end > self.sent and reply[:end].strip():
            self.sent = end
            self.emit(reply[:end].strip())
