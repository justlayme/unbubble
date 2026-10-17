"""Unbubble — the local helper that lets thegreymirror.com export iMessages.

The helper runs quietly in the background on the user's Mac or PC. It owns the two things a
web page cannot do on its own: talk to an iPhone over USB, and read the Mac's Messages
database. Everything the user sees lives on thegreymirror.com; this process only answers a
locked-down local WebSocket that accepts thegreymirror.com as its origin.
"""

__version__ = "1.1.0"
PROTOCOL_VERSION = 1
