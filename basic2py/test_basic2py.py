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
from transpiler import Basic2Dialect, Basic7Dialect, Basic65Dialect, TranspilerError
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
        try:
            basic = PyToBasic(dialect).transpile(python_source)
        except TranspilerError as e:
            record(False, f"[RT {label}] {name}", f"py2basic rejected it: {e}")
            continue
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


# ─────────────────────────────────────────────────────────────────────────────
# Keyword coverage
# ─────────────────────────────────────────────────────────────────────────────

# ── String functions ──────────────────────────────────────────────────────
check("LEFT$", "10 A$ = LEFT$(B$, 3)", contains=["[:3]"])
check("RIGHT$", "10 A$ = RIGHT$(B$, 3)", contains=["[-(3):]"])
check("MID$ with length", "10 A$ = MID$(B$, 2, 3)", contains=["(2) - 1"])
check("MID$ without length", "10 A$ = MID$(B$, 2)", contains=["(2) - 1:]"])
check("VAL", "10 A = VAL(B$)", contains=["float(b_str)"])
check(
    "nested string functions", "10 A = VAL(LEFT$(B$, 2))", contains=["float(", "[:2]"]
)

# ── Numeric intrinsics get runtime shims ─────────────────────────────────
check("RND", "10 A = RND(1)", contains=["_rnd(1)", "def _rnd", "import random"])
check("SGN", "10 A = SGN(B)", contains=["_sgn(b)", "def _sgn"])
check("PEEK", "10 A = PEEK(1024)", contains=["_peek(1024)", "def _peek"])
check("shims only when used", "10 A = 1", absent=["def _rnd", "def _sgn"])

# ── Hardware statements become TODOs, not silent no-ops ─────────────────
check("POKE", "10 POKE 53280, 0", contains=["# TODO", "POKE"])
check("SYS", "10 SYS 49152", contains=["# TODO", "SYS"])
check("WAIT", "10 WAIT 1, 2", contains=["# TODO", "WAIT"])
check("STOP", "10 STOP", contains=["sys.exit()", "import sys"])

# ── Arrays ───────────────────────────────────────────────────────────────
check("DIM numeric", "10 DIM A(10)", contains=["a_arr = [0] * 11"])
check("DIM string", "10 DIM A$(4)", contains=['a_str_arr = [""] * 5'])
check(
    "DIM several", "10 DIM A(2), B(3)", contains=["a_arr = [0] * 3", "b_arr = [0] * 4"]
)
check("array assignment", "10 DIM A(5)\n20 A(2) = 7", contains=["a_arr[2] = 7"])
check("array read", "10 DIM A(5)\n20 B = A(2)", contains=["b = a_arr[2]"])
# A and A(0) are different variables in BASIC; they must not fuse.
check(
    "array and scalar of the same letter stay separate",
    "10 DIM A(5)\n20 A = 1\n30 A(2) = 7",
    contains=["a = 1", "a_arr[2] = 7"],
)

# ── DATA / READ / RESTORE ────────────────────────────────────────────────
check(
    "DATA and READ",
    "10 DATA 1, 2, 3\n20 READ V",
    contains=["_DATA = [1, 2, 3]", "v = _read()", "def _read"],
)
check("DATA with words", "10 DATA ONE, TWO\n20 READ V$", contains=['"ONE"', '"TWO"'])
check(
    "READ several",
    "10 DATA 1, 2\n20 READ A, B",
    contains=["a = _read()", "b = _read()"],
)
check(
    "RESTORE",
    "10 DATA 1\n20 READ A\n30 RESTORE",
    contains=["_restore()", "def _restore"],
)

# ── GET / GETKEY / DEF FN ────────────────────────────────────────────────
check("GET", "10 GET K$", contains=["# TODO: GET"])
check("GETKEY", "10 GETKEY K$", contains=["input()[:1]"])
check(
    "DEF FN",
    "10 DEF FNA(X) = X * X\n20 B = FNA(3)",
    contains=["def fna(x):", "return x * x"],
)

# ── ON x GOTO / GOSUB ────────────────────────────────────────────────────
check(
    "ON GOTO",
    "10 ON X GOTO 100, 110\n100 PRINT 1\n110 PRINT 2",
    contains=["if x == 1:", "elif x == 2:", "# TODO"],
)
check(
    "ON GOSUB",
    "10 ON X GOSUB 100\n20 END\n100 PRINT 1\n110 RETURN",
    contains=["if x == 1:", "sub_100()"],
)

# ── Single-line ELSE ─────────────────────────────────────────────────────
check(
    "single-line ELSE",
    '10 IF X = 1 THEN PRINT "A" : ELSE PRINT "B"',
    contains=['print("A")', "else:", 'print("B")'],
)
check(
    "single-line ELSE with several statements",
    "10 IF X = 1 THEN A = 1 : B = 2 : ELSE A = 3",
    contains=["a = 1", "b = 2", "else:", "a = 3"],
)

# ── Unknown calls are reported rather than passed through ───────────────
check(
    "unknown function flagged",
    "10 A = SPRCOLOR(1)",
    contains=["# TODO: unconverted BASIC calls", "SPRCOLOR"],
)
check(
    "known functions are not flagged",
    "10 A = INT(B)",
    absent=["unconverted BASIC calls"],
)

# ── Blocks containing only TODOs still parse ────────────────────────────
check(
    "if body of only TODOs gets pass", "10 IF X = 1 THEN SYS 49152", contains=["pass"]
)

# ── Round trips for the new keywords ────────────────────────────────────
check_round_trip(
    "string slicing",
    """
    s = "hello world"
    print(s[:5])
    print(s[-5:])
    print(s[6:11])
    print(s[1])
""",
)

check_round_trip(
    "arrays",
    """
    xs = [0] * 5
    for i in range(5):
        xs[i] = i * 2
    total = 0
    for i in range(5):
        total += xs[i]
    print(total)
""",
)

check_round_trip(
    "continue and break",
    """
    for i in range(6):
        if i == 2:
            continue
        if i == 4:
            break
        print(i)
    print("done")
""",
)

check_round_trip(
    "chained comparison",
    """
    for i in range(6):
        if 1 < i < 4:
            print(i)
""",
)

check_round_trip(
    "val",
    """
    n = "42"
    x = int(n)
    print(x + 1)
""",
)

# ─────────────────────────────────────────────────────────────────────────────
# Expression conversion is structural, not regex-deep
# ─────────────────────────────────────────────────────────────────────────────
check(
    "nested LEN inside LEFT$",
    "10 PRINT LEFT$(S$, LEN(S$) - 1)",
    contains=["[:len(s_str) - 1]"],
)
check(
    "nested calls in MID$",
    "10 A$ = MID$(B$, INT(X / 2), LEN(B$))",
    contains=["[(int(x / 2)) - 1:(int(x / 2)) - 1 + (len(b_str))]"],
)
check(
    "INSTR with a nested call",
    "10 A = INSTR(B$, CHR$(65))",
    contains=[".find(chr(65)) + 1"],
)
check(
    "INSTR with a start",
    "10 A = INSTR(B$, C$, 3)",
    contains=[".find(c_str, (3) - 1) + 1"],
)
check(
    "UPPER$ of a slice",
    "10 A$ = UPPER$(LEFT$(B$, 2))",
    contains=["((b_str)[:2]).upper()"],
)
check(
    "MOD of nested calls",
    "10 A = MOD(INT(B), LEN(C$))",
    contains=["(int(b)) % (len(c_str))"],
)
check(
    "LOG inside LEFT$ is not renamed twice",
    "10 A$ = LEFT$(STR$(LOG(X)), 3)",
    contains=["math.log(x)"],
    absent=["math.math"],
)
check(
    "array index with a call",
    "10 DIM A(9)\n20 B = A(INT(X / 2))",
    contains=["a_arr[int(x / 2)]"],
)
check(
    "keywords inside strings are left alone",
    '10 PRINT "A AND B ^ LEN(X) <> 1"',
    contains=['"A AND B ^ LEN(X) <> 1"'],
    absent=["unconverted"],
)
check("backslash in a string is escaped", '10 PRINT "a\\b"', contains=['"a\\\\b"'])
check(
    "CHR$(13) becomes a newline escape",
    '10 PRINT "a" + CHR$(13) + "b"',
    contains=['"a\\nb"'],
)
check(
    "DEF FN call site is renamed",
    "10 DEF FNA(X) = X * 2\n20 Y = FNA(4)",
    contains=["y = fna(4)"],
    absent=["FNA"],
)
check(
    "GOSUB names are not flagged",
    "10 GOSUB 30\n20 END\n30 REM -- banner\n40 PRINT 1\n50 RETURN",
    absent=["unconverted"],
)

# ─────────────────────────────────────────────────────────────────────────────
# Statements
# ─────────────────────────────────────────────────────────────────────────────
check(
    "END inside IF imports sys",
    "10 IF X = 1 THEN END",
    contains=["import sys", "sys.exit()"],
)
check(
    "separator END does not import sys",
    "10 GOSUB 30\n20 END\n30 PRINT 1\n40 RETURN",
    absent=["import sys"],
)
check(
    "INPUT several variables",
    "10 INPUT A, B$",
    contains=["a = float(input())", "b_str = input()"],
)
check(
    "prompted INPUT of several variables",
    '10 INPUT "X"; A, B',
    contains=['a = float(input("X"))', "b = float(input())"],
)
check(
    "INPUT into an array element",
    "10 DIM A(3)\n20 INPUT A(1)",
    contains=["a_arr[1] = float(input())"],
)
check(
    "print default separator folds back",
    '10 PRINT "a" ; " " ; "b"',
    contains=['print("a", "b")'],
)
check(
    "early RETURN in a subroutine",
    "10 GOSUB 30\n20 END\n30 IF X = 1 THEN RETURN\n40 PRINT 1\n50 RETURN",
    contains=["return", "print(1)"],
)
check("RETURN outside a subroutine is a TODO", "10 RETURN", contains=["# TODO: RETURN"])

# ─────────────────────────────────────────────────────────────────────────────
# Control flow recovery
# ─────────────────────────────────────────────────────────────────────────────
check(
    "IF THEN GOTO forward is a negated if",
    '10 IF X = 1 THEN GOTO 40\n20 PRINT "no"\n30 PRINT "no2"\n40 PRINT "yes"',
    contains=["if not (x == 1):", 'print("no")', 'print("no2")'],
    absent=["# TODO"],
)
check(
    "IF THEN line number",
    '10 IF X = 1 THEN 30\n20 PRINT "no"\n30 PRINT "yes"',
    contains=["if not (x == 1):"],
)
check(
    "IF THEN GOTO out of a FOR is a break",
    "10 FOR I = 1 TO 9\n20 IF I = 5 THEN GOTO 50\n30 PRINT I\n40 NEXT I\n50 PRINT 1",
    contains=["if i == 5:", "break"],
)
check(
    "IF THEN GOTO backwards is a TODO",
    "10 PRINT 1\n20 IF X = 1 THEN GOTO 10",
    contains=["if x == 1:", "# TODO: GOTO 10"],
)
check(
    "assert is recovered",
    '10 IF (X > 0) THEN GOTO 40\n20 PRINT "ASSERTION FAILED"\n30 END\n40 PRINT "ok"',
    contains=["assert x > 0", 'print("ok")'],
    absent=["sys.exit", "pass"],
)
check(
    "assert with a message",
    '10 IF (X > 0) THEN GOTO 40\n20 PRINT "bad x"\n30 END\n40 PRINT "ok"',
    contains=['assert x > 0, "bad x"'],
)
check(
    "BEND : ELSE BEGIN",
    '10 IF X = 1 THEN BEGIN\n20 PRINT "a"\n30 BEND : ELSE BEGIN\n40 PRINT "b"\n50 BEND',
    contains=['print("a")', "else:", 'print("b")'],
    absent=["# TODO"],
)
check(
    "BEND ELSE BEGIN without the colon",
    '10 IF X = 1 THEN BEGIN\n20 PRINT "a"\n30 BEND ELSE BEGIN\n40 PRINT "b"\n50 BEND',
    contains=["else:"],
)
check(
    "while with the test as the loop top",
    "10 IF NOT (X < 5) THEN GOTO 40\n20 X = X + 1\n30 GOTO 10\n40 PRINT X",
    contains=["while x < 5:"],
    absent=["# TODO"],
)
check(
    "REM-topped while",
    "10 REM\n20 IF NOT (X < 5) THEN GOTO 50\n30 X = X + 1\n40 GOTO 10\n50 PRINT X",
    contains=["while x < 5:"],
    absent=["# TODO"],
)
check(
    "while not",
    "10 IF (D <> 0) THEN GOTO 40\n20 D = 1\n30 GOTO 10\n40 PRINT D",
    contains=["while not (d != 0):"],
)
check(
    "while True",
    "10 PRINT 1\n20 IF X = 1 THEN GOTO 40\n30 GOTO 10\n40 PRINT 2",
    contains=["while True:", "if x == 1:", "break"],
    absent=["# TODO"],
)
check(
    "if at the top of a while True body",
    "10 IF NOT (X = 1) THEN GOTO 30\n20 PRINT 1\n30 GOTO 10",
    contains=["while True:", "if x == 1:"],
)
check(
    "nested if/else keeps its structure",
    "\n".join(
        [
            "10 IF NOT (A > 0) THEN GOTO 90",
            "20 IF NOT (B > 0) THEN GOTO 70",
            '30 PRINT "x"',
            "40 GOTO 80",
            '50 PRINT "y"',
            '60 PRINT "y2"',
            '70 PRINT "z"',
            '80 PRINT "end"',
        ]
    ),
    contains=["    if b > 0:", '        print("x")', '    print("z")'],
)

# ─────────────────────────────────────────────────────────────────────────────
# Round trips for the fixed shapes
# ─────────────────────────────────────────────────────────────────────────────
check_round_trip(
    "nested if/else inside if",
    """
    a = 1
    b = 0
    if a > 0:
        if b > 0:
            print("x")
        else:
            print("y")
        print("z")
    print("end")
""",
)

check_round_trip(
    "assert that passes",
    """
    x = 5
    assert x > 0, "bad x"
    print("ok")
""",
)

check_round_trip(
    "precedence",
    """
    a = 7
    b = 2
    print((a + 1) * b)
    print(a - (b - 1))
    print(2 ** (1 + 1))
    print(a // (b + 1))
    a *= b + 1
    print(a)
""",
)

check_round_trip(
    "truthiness of bare values",
    """
    n = 3
    while n:
        print(n)
        n -= 1
    x = 5
    y = 2
    if x and y:
        print("both")
    if not x:
        print("zero")
    else:
        print("nonzero")
""",
)

check_round_trip(
    "while not",
    """
    done = 0
    n = 0
    while not done:
        n += 1
        if n == 3:
            done = 1
    print(n)
""",
)

check_round_trip(
    "string list",
    """
    names = ["ann", "bob"]
    first = names[0]
    print(first)
    print(names[1])
""",
)

check_round_trip(
    "string with a newline and a quote",
    """
    print("a\\nb")
    print("say \\"hi\\"")
""",
)

check_round_trip(
    "print spacing between strings",
    """
    print("Hello,", "world")
    x = 5
    print("x =", x)
""",
)

check_round_trip(
    "chained assignment and while True",
    """
    a = b = 0
    while True:
        a += 1
        if a == 3:
            break
    print(a + b)
""",
)

check_round_trip(
    "early return from a subroutine",
    """
    x = 1
    def check():
        if x == 1:
            print("one")
            return
        print("other")
    check()
""",
)

check_round_trip(
    "break from a loop that ends an if body",
    """
    x = 1
    if x == 1:
        for i in range(5):
            if i == 2:
                break
            print(i)
    print("done")
""",
)

check_round_trip(
    "slice to the end but one",
    """
    s = "hello"
    print(s[:-1])
    print(s[1:-1])
""",
)

print(f"\n{'=' * 50}")
print(f"Results: {PASS} passed, {FAIL} failed")
if FAIL:
    sys.exit(1)
