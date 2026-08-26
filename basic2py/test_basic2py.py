#!/usr/bin/env python3
"""
basic2py test suite — BASIC to Python, plus py2basic round-trip checks.
"""

import ast
import io
import os
import subprocess
import sys
import textwrap

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from basic2py import Transpiler as BasicToPy
from transpiler import Basic2Dialect, Basic7Dialect, Basic65Dialect
from transpiler import Transpiler as PyToBasic

PASS = 0
FAIL = 0


def record(ok, label, detail=""):
    global PASS, FAIL
    if ok:
        print(f"PASS {label}")
        PASS += 1
    else:
        print(f"FAIL {label}{': ' + detail if detail else ''}")
        FAIL += 1


def convert(basic_source):
    """Run basic2py, silencing its stderr warnings."""
    stderr, sys.stderr = sys.stderr, io.StringIO()
    try:
        return BasicToPy().transpile(textwrap.dedent(basic_source).strip())
    finally:
        sys.stderr = stderr


def check(name, basic_source, contains=(), absent=()):
    """Convert BASIC to Python and assert the result parses and matches."""
    result = convert(basic_source)
    problems = []
    try:
        ast.parse(result)
    except SyntaxError as e:
        problems.append(f"generated Python does not parse: line {e.lineno}: {e.msg}")
    problems += [f"missing {f!r}" for f in contains if f not in result]
    problems += [f"unexpected {f!r}" for f in absent if f in result]
    record(not problems, f"[B2P] {name}", "; ".join(problems))
    if problems or "--verbose" in sys.argv:
        print(textwrap.indent(result, "    "))


def run_python(source):
    """Execute Python source and return its stdout."""
    proc = subprocess.run(
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip())
    return proc.stdout


def check_round_trip(name, python_source):
    """Python -> BASIC -> Python must preserve observable behaviour."""
    python_source = textwrap.dedent(python_source).strip()
    try:
        expected = run_python(python_source)
    except RuntimeError as e:
        record(False, f"[RT] {name}", f"source program failed: {e}")
        return

    for label, dialect in (
        ("B20", Basic2Dialect),
        ("B65", Basic65Dialect),
        ("B70", Basic7Dialect),
    ):
        t = PyToBasic()
        t.dialect = dialect(t)
        basic = t.transpile(python_source)
        recovered = convert(basic)
        try:
            actual = run_python(recovered)
        except RuntimeError as e:
            record(False, f"[RT {label}] {name}", f"recovered program failed: {e}")
            print(textwrap.indent(basic, "    BAS "))
            print(textwrap.indent(recovered, "    PY  "))
            continue
        ok = actual == expected
        record(ok, f"[RT {label}] {name}", f"{expected!r} != {actual!r}")
        if not ok:
            print(textwrap.indent(basic, "    BAS "))
            print(textwrap.indent(recovered, "    PY  "))


# ── String literals keep their case ─────────────────────────────────────────
check(
    "string case preserved",
    '10 PRINT "Hello World"',
    contains=['"Hello World"'],
    absent=['"HELLO WORLD"'],
)
check("keywords still uppercased", '10 print "Hi"', contains=['print("Hi")'])

# ── CHR$(34) becomes an escaped quote, not a syntax error ──────────────────
check(
    "chr34 in middle",
    '10 PRINT "say " + CHR$(34) + "hi" + CHR$(34)',
    contains=['\\"hi\\"'],
)
check("chr34 leading", '10 PRINT CHR$(34) + "q"', contains=['"\\"q"'])
check("chr34 trailing", '10 PRINT "q" + CHR$(34)', contains=['"q\\""'])

# ── `=` becomes `==` only outside string literals ──────────────────────────
check(
    "equals inside string literal",
    '10 IF A$ = "x=y" THEN PRINT "EQ"',
    contains=['"x=y"'],
    absent=['"x==y"'],
)

# ── PRINT argument splitting respects nesting ──────────────────────────────
check("comma inside call", "10 PRINT MOD(A, 3)", contains=["%"], absent=["MOD("])
check("semicolon separator", '10 PRINT "a" ; "b"', contains=['print("a", "b")'])
check("trailing semicolon", '10 PRINT "a";', contains=["end=''"])

# ── Numeric INPUT reads a number ──────────────────────────────────────────
check("numeric input", "10 INPUT A", contains=["float(input())"])
check("string input", "10 INPUT A$", contains=["a_str = input()"], absent=["float("])
check("prompted numeric input", '10 INPUT "AGE"; A', contains=['float(input("AGE"))'])

# ── FOR bounds follow the step direction ─────────────────────────────────
check(
    "ascending for",
    "10 FOR I = 1 TO 10\n20 PRINT I\n30 NEXT I",
    contains=["range(1, 11)"],
)
check(
    "descending for",
    "10 FOR I = 10 TO 1 STEP -1\n20 PRINT I\n30 NEXT I",
    contains=["range(10, 0, -1)"],
    absent=["range(10, 2, -1)"],
)
check(
    "descending for by 3",
    "10 FOR I = 10 TO 1 STEP -3\n20 PRINT I\n30 NEXT I",
    contains=["range(10, 0, -3)"],
)
check(
    "bare NEXT",
    "10 FOR I = 1 TO 3\n20 PRINT I\n30 NEXT",
    contains=["range(1, 4)", "print(i)"],
)

# ── Colon-separated statements ───────────────────────────────────────────
check(
    "multi statement line",
    "10 X = 5 : Y = 6",
    contains=["x = 5", "y = 6"],
    absent=["x = 5 : y = 6"],
)
check("colon inside string kept", '10 PRINT "a:b"', contains=['"a:b"'])
check(
    "REM keeps its colons",
    "10 REM note: this is prose",
    contains=["# note: this is prose"],
)
check(
    "IF THEN with colons",
    '10 IF X = 1 THEN PRINT "a" : PRINT "b"',
    contains=['print("a")', 'print("b")'],
)
check(
    "FOR on one line",
    "10 FOR I = 1 TO 3 : PRINT I : NEXT I",
    contains=["range(1, 4)", "print(i)"],
)

# ── DO loops ─────────────────────────────────────────────────────────────
check(
    "DO WHILE tests at top",
    "10 DO WHILE X < 5\n20 X = X + 1\n30 LOOP",
    contains=["while x < 5:"],
    absent=["while True:"],
)
check(
    "DO UNTIL tests at top",
    "10 DO UNTIL X > 5\n20 X = X + 1\n30 LOOP",
    contains=["while not (x > 5):"],
)
check(
    "LOOP WHILE stays bottom-tested",
    "10 DO\n20 X = X + 1\n30 LOOP WHILE X < 5",
    contains=["while True:", "break"],
)
check("EXIT becomes break", "10 DO WHILE X < 5\n20 EXIT\n30 LOOP", contains=["break"])

# ── The END-detection heuristic ignores prose ───────────────────────────
check("REM mentioning END", "10 REM THE END\n20 PRINT 1", absent=["import sys"])
check("real END imports sys", "10 PRINT 1\n20 END", contains=["import sys"])

# ── Round trips through every dialect ──────────────────────────────────
check_round_trip(
    "fizzbuzz",
    """
    i = 1
    while i <= 20:
        m3 = int(i / 3) * 3
        m5 = int(i / 5) * 5
        if m3 == i and m5 == i:
            print("FIZZBUZZ")
        elif m3 == i:
            print("FIZZ")
        elif m5 == i:
            print("BUZZ")
        else:
            print(i)
        i += 1
""",
)

check_round_trip(
    "for loop with accumulator",
    """
    total = 0
    for i in range(1, 11):
        total += i
    print(total)
""",
)

check_round_trip(
    "descending loop",
    """
    for i in range(5, 0, -1):
        print(i)
""",
)

check_round_trip(
    "nested conditionals",
    """
    for i in range(1, 6):
        if i < 3:
            print("LOW")
        elif i < 5:
            print("MID")
        else:
            print("HIGH")
""",
)

check_round_trip(
    "subroutine",
    """
    def banner():
        print("====")
        print("HI")
        print("====")
    banner()
    print("AFTER")
""",
)

check_round_trip(
    "string handling",
    """
    greeting = "Hello There"
    print(greeting)
    print(len(greeting))
""",
)

check_round_trip(
    "while loop that never runs",
    """
    x = 99
    while x < 5:
        print("NEVER")
        x += 1
    print("DONE")
""",
)

print(f"\n{'=' * 50}")
print(f"Results: {PASS} passed, {FAIL} failed")
if FAIL:
    sys.exit(1)
