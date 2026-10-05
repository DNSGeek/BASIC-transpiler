"""
py2basic_runtime — CPython implementations of the BASIC intrinsics.

The whole point of py2basic is to write and *test* a program in Python
before transpiling it. Intrinsics like PEEK, SGN and GET have no Python
equivalent, so without this module a program that uses them cannot be run
locally at all.

Import it and your program runs under CPython; transpile it and these calls
turn into the BASIC keywords they stand for:

    from py2basic_runtime import poke, peek, sgn

    poke(53280, 0)        # POKE 53280, 0
    border = peek(53280)  # PEEK(53280)
    d = sgn(-7)           # SGN(-7)

The memory model here is a plain dict, so POKE/PEEK round-trip but have no
effect on anything real. SYS is a no-op. These are stubs for testing
control flow, not an emulator.
"""

import sys as _sys

__all__ = [
    "basic",
    "data",
    "fre",
    "getkey",
    "peek",
    "poke",
    "pos",
    "read",
    "restore",
    "sgn",
    "spc",
    "stop",
    "sys_call",
    "tab",
    "wait",
]

#: Stand-in for the machine's address space. POKE/PEEK round-trip through it.
_MEMORY: dict = {}

#: The READ queue. Unlike BASIC's DATA, data() has to run before read().
_DATA: list = []
_DATA_POS = 0


def sgn(x):
    """SGN(x) — -1, 0 or 1."""
    return (x > 0) - (x < 0)


def peek(address):
    """PEEK(address). Reads back whatever poke() last wrote, else 0."""
    return _MEMORY.get(int(address), 0)


def poke(address, value):
    """POKE address, value. Stores into the stand-in memory only."""
    _MEMORY[int(address)] = int(value) & 0xFF


def sys_call(address):
    """SYS address. A no-op — there is no machine code to run."""


def wait(address, mask, xor=0):
    """WAIT address, mask[, xor]. Returns immediately rather than hanging."""


def getkey():
    """GET var$ — a non-blocking key read. Always empty here."""
    return ""


def data(*items):
    """DATA a, b, c. Appends to the READ queue."""
    _DATA.extend(items)


def read():
    """READ var. Raises when the DATA is exhausted, as BASIC would."""
    global _DATA_POS
    if len(_DATA) <= _DATA_POS:
        raise RuntimeError("?OUT OF DATA ERROR")
    _DATA_POS += 1
    return _DATA[_DATA_POS - 1]


def restore():
    """RESTORE — rewind the DATA pointer."""
    global _DATA_POS
    _DATA_POS = 0


def stop():
    """STOP — ?BREAK."""
    raise SystemExit("BREAK")


def pos(_x=0):
    """POS(x) — cursor column. Not tracked; always 0."""
    return 0


def fre(_x=0):
    """FRE(x) — free memory. Not modelled; always 0."""
    return 0


def tab(n):
    """TAB(n) inside PRINT."""
    return " " * int(n)


def spc(n):
    """SPC(n) inside PRINT."""
    return " " * int(n)


def basic(statement):
    """
    basic("...") — raw BASIC passthrough.

    Nothing to execute locally, so it just reports what would be emitted.
    """
    print(f"[basic] {statement}", file=_sys.stderr)
