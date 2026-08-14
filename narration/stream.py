"""Watching the DM write, without watching it work.

A table currently waits for the whole generation before one word appears,
because the reply is not shown as written — it is post-processed first. Hooks
are pulled out of it (twenty-five families of ``[[NAME: …]]``), dice hooks are
rolled and replaced by their results, and what is left is split into narration,
speech and roll cards. Every one of those needs the reply entire.

Streaming a PREVIEW does not, and the whole difficulty is one thing:

    the model writes its hooks in the middle of its prose.

So a naive stream shows the player ``[[COMBAT: damage | Kara | 7]]``, and worse,
shows it BEFORE the game has decided whether that is what happened. The hooks are
the machinery; a player seeing them is a player watching the DM's hands.

:class:`HookGuard` is the answer, and it is the reason this is a module rather
than three lines at the call site: deciding what is safe to show is a small
state machine over a stream that can be cut ANYWHERE — including between the two
brackets of ``[[``, which is exactly where a naive filter leaks.

Nothing here talks to the network by choice: the two ``iter_*_deltas`` readers
take an iterable of lines, so the whole path can be proven against a synthetic
stream with no model, no key and no GPU (``scripts/stream_smoke.py``).
"""
from __future__ import annotations

import json
from typing import Iterable, Iterator, Optional

#: What opens a hook, and what closes it. Every hook family in the backend uses
#: this pair — see the ``*_HOOK_PATTERN`` constants — so the guard needs to know
#: nothing about which families exist. That matters: a new hook family is added
#: every few weeks, and one the guard had not been told about would stream
#: straight through to the player.
_OPEN = "[["
_CLOSE = "]]"


class HookGuard:
    """Emit narration as it arrives; never emit a hook, or half of one.

    Feed it whatever the model produced since last time and it returns the text
    that is provably safe to show. Anything that might still turn out to be a
    hook is HELD — a trailing ``[`` could become ``[[``, a trailing ``]`` inside
    a hook could become ``]]`` — and released once the next chunk settles it.

    The held tail is at most one character, so the preview never lags the model
    by more than that.

    >>> g = HookGuard()
    >>> g.feed("The door gives. [[COMBAT: start | ")
    'The door gives. '
    >>> g.feed("The Watch]] Steel rings.")
    ' Steel rings.'
    >>> g.close()
    ''

    An unterminated hook is dropped rather than flushed. A generation that runs
    out of tokens mid-hook is the one case where "show what you have" would put
    machinery on the player's screen, and it is not rare — it is what happens
    every time the model hits its limit.

    >>> g = HookGuard()
    >>> g.feed("You strike. [[COMBAT: damage | Gruk")
    'You strike. '
    >>> g.close()
    ''
    """

    def __init__(self) -> None:
        self._held = ""            # text we cannot yet classify
        self._in_hook = False      # inside [[ … ]]
        self.hidden = 0            # characters of hook withheld, for telemetry

    def feed(self, chunk: str) -> str:
        """Take the next piece of the model's output; return what to show now."""
        buf = self._held + (chunk or "")
        self._held = ""
        out: list[str] = []
        while buf:
            if self._in_hook:
                end = buf.find(_CLOSE)
                if end < 0:
                    # Keep only what could still be the start of "]]" — the rest
                    # is hook body and is never shown, so it must not be carried
                    # forward or the buffer grows with the whole hook.
                    self.hidden += len(buf)
                    self._held = "]" if buf.endswith("]") else ""
                    self.hidden -= len(self._held)
                    buf = ""
                    break
                self.hidden += end + len(_CLOSE)
                buf = buf[end + len(_CLOSE):]
                self._in_hook = False
                continue
            start = buf.find(_OPEN)
            if start >= 0:
                out.append(buf[:start])
                buf = buf[start + len(_OPEN):]
                self.hidden += len(_OPEN)
                self._in_hook = True
                continue
            # No hook opens in what we have. A trailing "[" is the ambiguous
            # case and the only one worth holding: it is either ordinary prose
            # or the first half of an opening the next chunk completes.
            if buf.endswith("["):
                out.append(buf[:-1])
                self._held = "["
            else:
                out.append(buf)
            buf = ""
        return "".join(out)

    def close(self) -> str:
        """The end of the stream: flush what is safe, drop what is not."""
        tail = "" if self._in_hook else self._held
        self.hidden += len(self._held) - len(tail)
        self._held = ""
        self._in_hook = False
        return tail


def iter_sse_deltas(lines: Iterable[bytes | str]) -> Iterator[str]:
    """Content deltas out of an OpenAI-compatible ``stream: true`` response.

    The wire format is server-sent events: ``data: {json}`` per line, a blank
    line between them, and ``data: [DONE]`` at the end. Anything that is not a
    delta — keep-alive comments, role-only first frames, usage trailers — yields
    nothing rather than an empty string, so a caller can count what it got.
    """
    for raw in lines:
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        line = line.strip()
        if not line or line.startswith(":"):
            continue
        if line.startswith("data:"):
            line = line[5:].strip()
        if line == "[DONE]":
            return
        try:
            frame = json.loads(line)
        except ValueError:
            continue
        for choice in frame.get("choices") or ():
            piece = (choice.get("delta") or {}).get("content")
            if piece:
                yield piece


def iter_ollama_deltas(lines: Iterable[bytes | str]) -> Iterator[str]:
    """The same, for Ollama's native ``/api/chat`` — newline-delimited JSON.

    Kept apart from the SSE reader rather than sniffed at runtime: the native
    endpoint is what carries ``num_ctx`` (see LLM_NUM_CTX in the backend), so
    which one is in use is already a decision the caller has made.
    """
    for raw in lines:
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        line = line.strip()
        if not line:
            continue
        try:
            frame = json.loads(line)
        except ValueError:
            continue
        piece = (frame.get("message") or {}).get("content")
        if piece:
            yield piece
        if frame.get("done"):
            return


def guarded(deltas: Iterable[str], guard: Optional[HookGuard] = None) -> Iterator[str]:
    """Run a delta stream through a :class:`HookGuard`, skipping empty results.

    The convenience the callers all want: a stream of things to SHOW. A chunk
    that was entirely hook yields nothing at all rather than an empty string,
    so a transport does not send a frame per hook character.
    """
    g = guard or HookGuard()
    for piece in deltas:
        shown = g.feed(piece)
        if shown:
            yield shown
    tail = g.close()
    if tail:
        yield tail
