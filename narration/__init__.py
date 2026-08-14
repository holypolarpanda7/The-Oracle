"""Turning the DM's reply into something a player can watch arrive.

The backend's narration is not the model's output: hooks are pulled out of it,
dice are rolled and substituted inline, and the result is split into typed
events. All of that needs the WHOLE reply, which is why a table waits for the
entire generation before a single word appears.

This package holds the part that does not: reading a model's output as it is
written, and deciding what of it is safe to show yet.
"""
from .stream import HookGuard, iter_ollama_deltas, iter_sse_deltas

__all__ = ["HookGuard", "iter_sse_deltas", "iter_ollama_deltas"]
