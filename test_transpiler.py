#!/usr/bin/env python3
"""
py2basic test suite — tests both BASIC 2.0 and BASIC 65 dialects
"""

import os
import re
import sys
import textwrap

sys.path.insert(0, os.path.dirname(__file__))

from transpiler import (
    Basic2Dialect,
    Basic7Dialect,
    Basic65Dialect,
    LineAllocator,
    Transpiler,
    TranspilerError,
)

PASS = 0
FAIL = 0


def make_transpiler(basic65=False):
    t = Transpiler()
    t.dialect = Basic65Dialect(t) if basic65 else Basic2Dialect(t)
    return t


def test(name, source, expected=None, should_fail=False, basic65=False):
    global PASS, FAIL
    t = make_transpiler(basic65)
    label = f"[{'B65' if basic65 else 'B20'}] {name}"
    try:
        result = t.transpile(source.strip())
        if should_fail:
            print(f"FAIL {label}: Expected error but got:\n{result}")
            FAIL += 1
            return
        if expected:
            missing = [f for f in expected if f not in result]
            if missing:
                print(f"FAIL {label}: Missing: {missing}")
                print(f"  Got:\n{result}")
                FAIL += 1
                return
        print(f"PASS {label}")
        if "--verbose" in sys.argv:
            print(result)
            print()
        PASS += 1
    except TranspilerError as e:
        if should_fail:
            print(f"PASS {label} (correctly rejected: {e})")
            PASS += 1
        else:
            print(f"FAIL {label}: Unexpected error: {e}")
            FAIL += 1


# Shared tests run against both dialects
for b65 in (False, True):
    test("numeric assignment", "x = 42", ["= 42"], basic65=b65)
    test("float assignment", "x = 3.14", ["= 3.14"], basic65=b65)
    test("string assignment", 's = "hi"', ['"hi"'], basic65=b65)
    test("add", "x = 1 + 2", ["1 + 2"], basic65=b65)
    test("sub", "x = 5 - 3", ["5 - 3"], basic65=b65)
    test("mul", "x = 4 * 2", ["4 * 2"], basic65=b65)
    test("div", "x = 8 / 2", ["8 / 2"], basic65=b65)
    test("pow", "x = 2 ** 8", ["2 ^ 8"], basic65=b65)
    test("floordiv", "x = 7 // 2", ["INT(7 / 2)"], basic65=b65)
    test("aug add", "x = 0\nx += 1", ["= A + 1"], basic65=b65)
    test("aug sub", "x = 10\nx -= 3", ["= A - 3"], basic65=b65)
    test("print", 'print("hello")', ["PRINT"], basic65=b65)
    test("print empty", "print()", ["PRINT"], basic65=b65)
    test("str()", "x=5\ns=str(x)", ["STR$("], basic65=b65)
    test("len()", 's="hi"\nn=len(s)', ["LEN("], basic65=b65)
    test("chr()", "c=chr(65)", ["CHR$("], basic65=b65)
    test("ord()", "n=ord('A')", ["ASC("], basic65=b65)
    test("int()", "n=int(3.7)", ["INT("], basic65=b65)
    test(
        "for range(n)",
        "for i in range(5):\n print(i)",
        ["FOR", "= 0 TO 4", "NEXT"],
        basic65=b65,
    )
    test(
        "for range(a,b)", "for i in range(1,11):\n print(i)", ["= 1 TO 10"], basic65=b65
    )
    test(
        "for range step", "for i in range(0,20,2):\n print(i)", ["STEP 2"], basic65=b65
    )
    test(
        "function def",
        "def greet():\n print('hi')\ngreet()",
        ["GOSUB", "RETURN"],
        basic65=b65,
    )
    test("function params rejected", "def f(a): pass", should_fail=True, basic65=b65)
    test("return value rejected", "def f():\n return 1", should_fail=True, basic65=b65)
    test("list becomes DIM", "x=[1,2]", ["DIM A(1)"], basic65=b65)
    test("dict rejected", "x={'a':1}", should_fail=True, basic65=b65)
    test("class rejected", "class F: pass", should_fail=True, basic65=b65)
    test("try rejected", "try:\n pass\nexcept: pass", should_fail=True, basic65=b65)
    test(
        "continue supported",
        "i=0\nwhile i<10:\n i+=1\n continue",
        basic65=b65,
    )
    test(
        "for non-range rejected",
        "for x in [1,2]:\n print(x)",
        should_fail=True,
        basic65=b65,
    )


# BASIC 2.0 specific
test("B20 modulo", "x = 7 % 3", ["- INT("], basic65=False)
test("B20 aug mod", "x=7\nx%=3", ["- INT("], basic65=False)

test(
    "B20 if only",
    "x=5\nif x>3:\n print('big')",
    ["IF NOT (A > 3) THEN GOTO"],
    basic65=False,
)

test(
    "B20 if/else",
    "x=5\nif x>0:\n print('y')\nelse:\n print('n')",
    ["IF NOT (A > 0) THEN GOTO", "GOTO"],
    basic65=False,
)

test(
    "B20 elif",
    "x=5\nif x>10:\n print('b')\nelif x>5:\n print('m')\nelse:\n print('s')",
    ["IF NOT (A > 10) THEN GOTO", "IF NOT (A > 5) THEN GOTO"],
    basic65=False,
)

test(
    "B20 while",
    "i=0\nwhile i<10:\n i+=1",
    ["IF NOT (A < 10) THEN GOTO", "GOTO"],
    basic65=False,
)

test(
    "B20 break", "i=0\nwhile i<100:\n if i==5:\n  break\n i+=1", ["GOTO"], basic65=False
)

test("B20 assert", "x=5\nassert x>0", ["IF (A > 0) THEN GOTO", "END"], basic65=False)

test(
    "B20 sleep becomes a delay loop",
    "import time\ntime.sleep(1)",
    ["FOR ", " TO 1000", "NEXT "],
    basic65=False,
)


# BASIC 65 specific
test("B65 modulo", "x = 7 % 3", ["MOD(7, 3)"], basic65=True)
test("B65 aug mod", "x=7\nx%=3", ["MOD(A, 3)"], basic65=True)

test(
    "B65 if only",
    "x=5\nif x>3:\n print('big')",
    ["IF A > 3 THEN BEGIN", "BEND"],
    basic65=True,
)

test(
    "B65 if/else",
    "x=5\nif x>0:\n print('y')\nelse:\n print('n')",
    ["IF A > 0 THEN BEGIN", "BEND : ELSE BEGIN", "BEND"],
    basic65=True,
)

test(
    "B65 elif",
    "x=5\nif x>10:\n print('b')\nelif x>5:\n print('m')\nelse:\n print('s')",
    ["IF A > 10 THEN BEGIN", "BEND : ELSE BEGIN", "BEND"],
    basic65=True,
)

test("B65 while", "i=0\nwhile i<10:\n i+=1", ["DO WHILE A < 10", "LOOP"], basic65=True)

test(
    "B65 break", "i=0\nwhile i<100:\n if i==5:\n  break\n i+=1", ["EXIT"], basic65=True
)

test("B65 sleep", "import time\ntime.sleep(1.5)", ["SLEEP 1.5"], basic65=True)


# Cross-dialect smoke: fizzbuzz
FIZZBUZZ = """
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
"""

test("fizzbuzz", FIZZBUZZ, ["IF NOT", "GOTO"], basic65=False)
test("fizzbuzz", FIZZBUZZ, ["DO WHILE", "LOOP", "BEGIN", "BEND"], basic65=True)


# BASIC 7.0 specific — structured IF/WHILE like B65, no MOD() like B20
def make_b7():
    t = Transpiler()
    t.dialect = Basic7Dialect(t)
    return t


def test7(name, source, expected=None, should_fail=False):
    global PASS, FAIL
    t = make_b7()
    label = f"[B70] {name}"
    try:
        result = t.transpile(source.strip())
        if should_fail:
            print(f"FAIL {label}: Expected error but got:\n{result}")
            FAIL += 1
            return
        if expected:
            missing = [f for f in expected if f not in result]
            if missing:
                print(f"FAIL {label}: Missing: {missing}")
                print(f"  Got:\n{result}")
                FAIL += 1
                return
        print(f"PASS {label}")
        if "--verbose" in sys.argv:
            print(result)
            print()
        PASS += 1
    except TranspilerError as e:
        if should_fail:
            print(f"PASS {label} (correctly rejected: {e})")
            PASS += 1
        else:
            print(f"FAIL {label}: Unexpected error: {e}")
            FAIL += 1


# Structured like B65
test7("if only", "x=5\nif x>3:\n print('big')", ["IF A > 3 THEN BEGIN", "BEND"])
test7("if/else", "x=5\nif x>0:\n print('y')\nelse:\n print('n')", ["BEND : ELSE BEGIN"])
test7(
    "elif",
    "x=5\nif x>10:\n print('b')\nelif x>5:\n print('m')\nelse:\n print('s')",
    ["IF A > 10 THEN BEGIN", "BEND : ELSE BEGIN"],
)
test7("while", "i=0\nwhile i<10:\n i+=1", ["DO WHILE A < 10", "LOOP"])
test7("break", "i=0\nwhile i<100:\n if i==5:\n  break\n i+=1", ["EXIT"])

# No MOD() like B20
test7("modulo", "x = 7 % 3", ["- INT("])
test7("aug mod", "x=7\nx%=3", ["- INT("])

# SLEEP exists, but takes whole seconds only
test7("whole-second sleep uses SLEEP", "import time\ntime.sleep(1)", ["SLEEP 1"])

# Shared basics still work
test7("for loop", "for i in range(5):\n print(i)", ["FOR", "= 0 TO 4", "NEXT"])
test7("function", "def f():\n print('hi')\nf()", ["GOSUB", "RETURN"])
test7("fizzbuzz", FIZZBUZZ, ["DO WHILE", "LOOP", "BEGIN", "BEND"])


# % string formatting
test("percent %s", 'name="Tom"\nprint("Hi %s" % name)', ['"Hi " + A$'], basic65=False)
test("percent %d", 'print("Age %d" % 30)', ["STR$(INT(30))"], basic65=False)
test(
    "percent multi",
    'name="Tom"\nage=30\nprint("Hi %s age %d" % (name, age))',
    ['"Hi " + A$', "STR$(INT(A))"],
    basic65=False,
)
test("percent numeric as str", 'x=42\nprint("val %s" % x)', ["STR$("], basic65=False)


# f-string support
test(
    "fstring str var", 'name="Tom"\nprint(f"Hi {name}")', ['"Hi " + A$'], basic65=False
)
test("fstring numeric", 'age=30\nprint(f"Age {age}")', ["STR$(A)"], basic65=False)
test(
    "fstring mixed",
    'name="Tom"\nage=30\nprint(f"Hi {name} age {age}")',
    ['"Hi " + A$', "STR$(A)"],
    basic65=False,
)
test("fstring no vars", 'print(f"Hello there")', ['"Hello there"'], basic65=False)
test(
    "fstring format spec rejected",
    'x=3.14\nprint(f"{x:.2f}")',
    should_fail=True,
    basic65=False,
)

# ─────────────────────────────────────────────────────────────────────────────
# Regression tests
# ─────────────────────────────────────────────────────────────────────────────

DIALECTS = {"B20": Basic2Dialect, "B65": Basic65Dialect, "B70": Basic7Dialect}


def dangling_jumps(result):
    """Every GOTO/GOSUB target must be a line that actually exists."""
    present = {int(m.group(1)) for m in re.finditer(r"^(\d+) ", result, re.MULTILINE)}
    return sorted(
        {
            f"{m.group(1)} {m.group(2)} has no such line"
            for m in re.finditer(r"\b(GOTO|GOSUB)\s+(\d+)", result)
            if int(m.group(2)) not in present
        }
    )


def check(name, source, dialect="B20", contains=(), absent=(), should_fail=False):
    """Transpile and assert on the result. Also verifies every jump resolves."""
    global PASS, FAIL
    label = f"[{dialect}] {name}"
    t = Transpiler()
    t.dialect = DIALECTS[dialect](t)
    try:
        result = t.transpile(textwrap.dedent(source).strip())
    except TranspilerError as e:
        if should_fail:
            print(f"PASS {label} (correctly rejected: {e})")
            PASS += 1
        else:
            print(f"FAIL {label}: Unexpected error: {e}")
            FAIL += 1
        return

    if should_fail:
        print(f"FAIL {label}: Expected error but got:\n{result}")
        FAIL += 1
        return

    problems = [f"missing {f!r}" for f in contains if f not in result]
    problems += [f"unexpected {f!r}" for f in absent if f in result]
    problems += dangling_jumps(result)
    if problems:
        print(f"FAIL {label}: {'; '.join(problems)}")
        print(f"  Got:\n{result}")
        FAIL += 1
        return
    print(f"PASS {label}")
    if "--verbose" in sys.argv:
        print(result)
        print()
    PASS += 1


def check_all(name, source, **kw):
    for dialect in DIALECTS:
        check(name, source, dialect=dialect, **kw)


# ── while must test at the top, not the bottom ──────────────────────────────
for d in ("B65", "B70"):
    check(
        "while tests at top",
        "i=0\nwhile i<10:\n i+=1",
        dialect=d,
        contains=["DO WHILE A < 10", "LOOP"],
        absent=["LOOP WHILE"],
    )

# ── no jump may land past the end of the program ────────────────────────────
check_all("if as last statement", "x=5\nif x>3:\n print('big')")
check_all("if/else as last statement", "x=5\nif x>0:\n print('y')\nelse:\n print('n')")
check_all(
    "elif chain as last statement",
    "x=1\nif x>3:\n print(1)\nelif x>2:\n print(2)\nelse:\n print(3)",
)
check_all("while as last statement", "i=0\nwhile i<3:\n i+=1")
check_all("assert as last statement", "x=5\nassert x>0")
check_all(
    "nested if in while",
    """
    i = 0
    while i < 10:
        if i == 3:
            print(i)
        i += 1
""",
)

# ── string type inference reaches a fixpoint ───────────────────────────────
check_all(
    "concat infers string",
    'name="w"\ngreeting="hi " + name\nprint(greeting)',
    contains=['B$ = "hi " + A$'],
    absent=['B = "'],
)
check_all(
    "chained concat infers string",
    'a="x"\nb=a+"y"\nc=b+"z"\nprint(c)',
    contains=["C$ ="],
)
check_all(
    "fstring infers string", 'name="w"\nmsg=f"hi {name}"\nprint(msg)', contains=["B$ ="]
)
check_all(
    "percent format infers string",
    'name="w"\nmsg="hi %s" % name\nprint(msg)',
    contains=["B$ ="],
)
check_all("aug concat infers string", 's="a"\ns += "b"', contains=["A$ ="])
check_all("mixed types rejected", 'v = 1\nv = "text"', should_fail=True)

# ── range() bounds and direction ──────────────────────────────────────────
check_all(
    "descending range",
    "for i in range(10, 0, -1):\n print(i)",
    contains=["FOR A = 10 TO 1 STEP -1"],
)
check_all(
    "descending range by 3",
    "for i in range(10, 0, -3):\n print(i)",
    contains=["FOR A = 10 TO 1 STEP -3"],
)
check_all(
    "descending range to negative",
    "for i in range(3, -4, -1):\n print(i)",
    contains=["TO -3 STEP -1"],
)
check_all(
    "statically empty range skipped",
    "for i in range(0):\n print(i)",
    contains=["REM EMPTY RANGE"],
    absent=["FOR "],
)
check_all(
    "runtime bound gets a zero-trip guard",
    "n = 0\nfor i in range(n):\n print(i)\nprint('after')",
    contains=["THEN GOTO"],
)
check_all(
    "known non-empty range needs no guard",
    "for i in range(5):\n print(i)",
    absent=["THEN GOTO"],
)
check_all(
    "non-literal step rejected",
    "s = 2\nfor i in range(0, 10, s):\n print(i)",
    should_fail=True,
)
check_all(
    "zero step rejected", "for i in range(0, 10, 0):\n print(i)", should_fail=True
)
check_all(
    "range keyword rejected", "for i in range(5, step=1):\n print(i)", should_fail=True
)

# ── break inside a for loop ───────────────────────────────────────────────
check_all(
    "break in for",
    """
    for i in range(3):
        if i == 1:
            break
        print(i)
    print("after")
""",
)
check_all(
    "break in nested for/while",
    """
    for i in range(3):
        j = 0
        while j < 3:
            if j == 1:
                break
            j += 1
        if i == 2:
            break
    print("done")
""",
)
check_all("break outside loop rejected", "break", should_fail=True)

# ── print() keyword arguments ─────────────────────────────────────────────
check_all("print end=''", "print('a', end='')", contains=['PRINT "a";'])
check_all(
    "print end='\\n'",
    "print('a', end='\\n')",
    contains=['PRINT "a"'],
    absent=['PRINT "a";'],
)
check_all("print sep", "print('b', 'c', sep='-')", contains=['"b" ; "-" ; "c"'])
check_all("print sep=''", "print('b', 'c', sep='')", contains=['"b" ; "c"'])
check_all(
    "print file= rejected", "import sys\nprint('a', file=sys.stderr)", should_fail=True
)
check_all(
    "print non-literal end rejected", "e='x'\nprint('a', end=e)", should_fail=True
)

# ── %% collapses to a literal % when the % operator is applied ──────────
# (A bare "100%%" literal really is 100%% in Python, so it stays put.)
check_all(
    "percent escape in format",
    "x=5\nprint('%d%% done' % x)",
    contains=["STR$(INT(A))", '"%"'],
    absent=["%%"],
)
check_all(
    "percent escape only",
    "print('%s%%' % 'hi')",
    contains=['"hi"', '"%"'],
    absent=["%%"],
)
check_all(
    "bare percent literal untouched", "print('100%% done')", contains=['"100%% done"']
)
check_all("too few format args rejected", "print('%s %s' % 'a')", should_fail=True)
check_all("too many format args rejected", "print('%s' % ('a', 'b'))", should_fail=True)

# ── builtin arity is checked rather than crashing ────────────────────────
check_all("int() no args rejected", "x = int()", should_fail=True)
check_all("chr() no args rejected", "x = chr()", should_fail=True)
check_all("abs() two args rejected", "x = abs(1, 2)", should_fail=True)
check_all("log() one arg", "x = log(8)", contains=["LOG(8)"])
check_all("log() two args", "x = log(8, 2)", contains=["(LOG(8) / LOG(2))"])
check_all("builtin keyword rejected", "x = int(3, base=10)", should_fail=True)

# ── nested and duplicate defs are rejected, not crashes ──────────────────
check_all(
    "nested def rejected",
    """
    if True:
        def f():
            print("hi")
        f()
""",
    should_fail=True,
)
check_all(
    "def inside def rejected",
    """
    def outer():
        def inner():
            print("hi")
        inner()
    outer()
""",
    should_fail=True,
)
check_all(
    "duplicate def rejected",
    """
    def f():
        print(1)
    def f():
        print(2)
    f()
""",
    should_fail=True,
)

# ── math module ─────────────────────────────────────────────────────────
check_all("math.sqrt", "import math\nx = math.sqrt(16)", contains=["SQR(16)"])
check_all("math alias", "import math as m\nx = m.cos(1)", contains=["COS(1)"])
check_all(
    "unknown math function rejected", "import math\nx = math.gamma(1)", should_fail=True
)

# ── UPPER$/LOWER$ only exist in BASIC 65 ────────────────────────────────
check("upper on B65", 's="hi"\nt=s.upper()', dialect="B65", contains=["UPPER$(A$)"])
check("lower on B65", 's="HI"\nt=s.lower()', dialect="B65", contains=["LOWER$(A$)"])
for d in ("B20", "B70"):
    check("upper rejected", 's="hi"\nt=s.upper()', dialect=d, should_fail=True)


# ── line numbering limits ───────────────────────────────────────────────
def check_allocator(name, start, step, should_fail):
    global PASS, FAIL
    try:
        LineAllocator(start, step)
        ok = not should_fail
    except TranspilerError:
        ok = should_fail
    print(f"{'PASS' if ok else 'FAIL'} [CLI] {name}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


check_allocator("step 0 rejected", 10, 0, True)
check_allocator("negative step rejected", 10, -10, True)
check_allocator("negative start rejected", -1, 10, True)
check_allocator("start beyond 63999 rejected", 70000, 10, True)
check_allocator("sane defaults accepted", 10, 10, False)


def check_overflow():
    global PASS, FAIL
    t = Transpiler()
    t.lines = LineAllocator(63900, 10)
    try:
        t.transpile("\n".join(f"x{i} = {i}" for i in range(30)))
        print("FAIL [CLI] line number overflow detected")
        FAIL += 1
    except TranspilerError:
        print("PASS [CLI] line number overflow detected")
        PASS += 1


check_overflow()

# ── modulo does not over-parenthesise simple operands ──────────────────
check(
    "B20 modulo of atoms", "x=7\nx%=3", dialect="B20", contains=["A - INT(A / 3) * 3"]
)
check(
    "B70 modulo of atoms", "x=7\nx%=3", dialect="B70", contains=["A - INT(A / 3) * 3"]
)


# ─────────────────────────────────────────────────────────────────────────────
# Keyword support
# ─────────────────────────────────────────────────────────────────────────────

# ── VAL: int()/float() of a string is not INT() ────────────────────────────
check_all(
    "int of a string uses VAL",
    'n = "42"\nx = int(n)',
    contains=["INT(VAL(A$))"],
    absent=["INT(A$)"],
)
check_all("float of a string uses VAL", 'n = "42"\nx = float(n)', contains=["VAL(A$)"])
check_all(
    "int of a number is unchanged",
    "x = int(3.7)",
    contains=["INT(3.7)"],
    absent=["VAL("],
)

# ── String slicing -> LEFT$ / RIGHT$ / MID$ ───────────────────────────────
check_all("slice to LEFT$", 's = "hello"\nt = s[:3]', contains=["LEFT$(A$, 3)"])
check_all("slice to RIGHT$", 's = "hello"\nt = s[-3:]', contains=["RIGHT$(A$, 3)"])
check_all("slice to MID$", 's = "hello"\nt = s[1:4]', contains=["MID$(A$, 2, 3)"])
check_all(
    "open-ended slice to MID$", 's = "hello"\nt = s[2:]', contains=["MID$(A$, 3)"]
)
check_all("index to MID$", 's = "hello"\nt = s[1]', contains=["MID$(A$, 2, 1)"])
check_all(
    "negative index to MID$",
    's = "hello"\nt = s[-1]',
    contains=["MID$(A$, LEN(A$), 1)"],
)
check_all(
    "slice from the end", 's = "hello"\nt = s[:-1]', contains=["LEFT$(A$, LEN(A$) - 1)"]
)
check_all("full slice is the string", 's = "hello"\nt = s[:]', contains=["B$ = A$"])
check_all(
    "variable slice bounds",
    's = "hello"\na=1\nb=3\nt = s[a:b]',
    contains=["MID$(A$, A + 1, B - A)"],
)
check_all("slice step rejected", 's = "hi"\nt = s[::2]', should_fail=True)
check_all(
    "sliced value is still a string",
    's = "hello"\nt = s[:2]\nu = t + "x"',
    contains=["C$ ="],
)

# ── Arrays -> DIM ─────────────────────────────────────────────────────────
check_all(
    "list literal dims and fills",
    "xs = [1, 2, 3]",
    contains=["DIM A(2)", "A(0) = 1", "A(2) = 3"],
)
check_all(
    "repeat list dims only", "xs = [0] * 4", contains=["DIM A(3)"], absent=["A(0) = 0"]
)
check_all("non-zero fill loops", "xs = [7] * 3", contains=["DIM A(2)", "FOR ", "NEXT "])
check_all("string list", 'xs = ["a", "b"]', contains=["DIM A$(1)", 'A$(0) = "a"'])
check_all("element assignment", "xs = [0] * 4\nxs[2] = 9", contains=["A(2) = 9"])
check_all(
    "element aug-assignment", "xs = [0] * 4\nxs[1] += 5", contains=["A(1) = A(1) + 5"]
)
check_all("element read", "xs = [0] * 4\ny = xs[1]", contains=["= A(1)"])
check_all("variable subscript", "xs = [0] * 4\ni = 1\ny = xs[i]", contains=["A(A)"])
check_all("len of a list folds", "xs = [0] * 7\nn = len(xs)", contains=["= 7"])
check_all(
    "range(len(xs)) needs no guard",
    "xs = [0] * 3\nfor i in range(len(xs)):\n print(xs[i])",
    contains=["TO 2"],
    absent=["THEN GOTO"],
)
check_all(
    "string array element is a string",
    'xs = ["a", "b"]\nt = xs[0] + "z"',
    contains=['+ "z"'],
)
check_all("out-of-range index rejected", "xs = [0] * 3\nxs[5] = 1", should_fail=True)
check_all("negative index rejected", "xs = [0] * 3\nxs[-1] = 1", should_fail=True)
check_all("empty list rejected", "xs = []", should_fail=True)
check_all("mixed-type list rejected", 'xs = [1, "a"]', should_fail=True)
check_all("re-dim rejected", "xs = [0] * 3\nxs = [0] * 4", should_fail=True)
check_all("bare array reference rejected", "xs = [0] * 3\ny = xs", should_fail=True)
check_all("list slicing rejected", "xs = [0] * 3\ny = xs[0:2]", should_fail=True)

# ── DEF FN ────────────────────────────────────────────────────────────────
check_all(
    "def fn",
    "def sq(x):\n return x * x\ny = sq(4)",
    contains=["DEF FNA(A) = A * A", "FNA(4)"],
)
check_all(
    "def fn precedes use",
    "y = 1\ndef sq(x):\n return x * x\nz = sq(y)",
    contains=["DEF FNA"],
)
check_all(
    "two def fns",
    "def a(x):\n return x\ndef b(y):\n return y\n" "z = a(1) + b(2)",
    contains=["FNA", "FNB"],
)
check_all(
    "def fn with docstring",
    'def sq(x):\n "doc"\n return x * x\ny=sq(2)',
    contains=["DEF FNA"],
)
check_all(
    "zero-arg def stays a subroutine",
    "def f():\n print(1)\nf()",
    contains=["GOSUB", "RETURN"],
    absent=["DEF FN"],
)
check_all(
    "multi-statement def with params rejected",
    "def f(x):\n print(x)\n return x",
    should_fail=True,
)
check_all("two-param def rejected", "def f(x, y):\n return x + y", should_fail=True)
check_all(
    "string-returning def fn rejected",
    'def f(x):\n return "a"\ny = f(1)',
    should_fail=True,
)
check_all(
    "def fn wrong arity rejected",
    "def sq(x):\n return x\ny = sq(1, 2)",
    should_fail=True,
)
check_all(
    "def fn called as a statement rejected",
    "def sq(x):\n return x\nsq(1)",
    should_fail=True,
)

# ── Hardware and DATA intrinsics ─────────────────────────────────────────
check_all("poke", "poke(53280, 0)", contains=["POKE 53280, 0"])
check_all("peek", "x = peek(1024)", contains=["PEEK(1024)"])
check_all("sys_call", "sys_call(49152)", contains=["SYS 49152"])
check_all("wait two args", "wait(1, 2)", contains=["WAIT 1, 2"])
check_all("wait three args", "wait(1, 2, 3)", contains=["WAIT 1, 2, 3"])
check_all("sgn", "x = sgn(-7)", contains=["SGN(-7)"])
check_all("stop", "stop()", contains=["STOP"])
check_all(
    "data and restore", "data(1, 2, 3)\nrestore()", contains=["DATA 1,2,3", "RESTORE"]
)
check_all("data with strings", 'data("a", "b")', contains=['DATA "a","b"'])
check_all("read into a variable", "data(1)\nx = read()", contains=["READ A"])
check_all("getkey", 'k = ""\nk = getkey()', contains=["GET A$"])
check_all("getkey into numeric rejected", "k = 0\nk = getkey()", should_fail=True)
check_all("poke arity checked", "poke(1)", should_fail=True)
check_all("data with a variable rejected", "x = 1\ndata(x)", should_fail=True)
check_all(
    "raw basic passthrough",
    'basic("CIRCLE 1,160,100,50")',
    contains=["CIRCLE 1,160,100,50"],
)
check_all("raw basic needs a literal", 's = "X"\nbasic(s)', should_fail=True)
check_all(
    "runtime import allowed",
    "from py2basic_runtime import sgn\nx = sgn(1)",
    contains=["SGN(1)"],
)

# ── random -> RND ────────────────────────────────────────────────────────
check_all("random.random", "import random\nx = random.random()", contains=["RND(1)"])
check_all(
    "random.randint",
    "import random\nd = random.randint(1, 6)",
    contains=["INT(RND(1) * (6 - 1 + 1)) + 1"],
)
check_all(
    "random.randrange",
    "import random\nx = random.randrange(10)",
    contains=["INT(RND(1) * (10))"],
)
check_all("random.seed", "import random\nrandom.seed(7)", contains=["RND(-ABS(7))"])
check_all("random alias", "import random as r\nx = r.random()", contains=["RND(1)"])
check_all(
    "unknown random function rejected",
    "import random\nx = random.gauss(0, 1)",
    should_fail=True,
)

# ── sleep on dialects without SLEEP ──────────────────────────────────────
check(
    "sleep delay loop on B20",
    "import time\ntime.sleep(2)",
    dialect="B20",
    contains=["FOR ", "TO 2000", "NEXT "],
)
check(
    "sleep delay loop on B70",
    "import time\ntime.sleep(0.5)",
    dialect="B70",
    contains=["TO 500"],
)
check(
    "real SLEEP on B65",
    "import time\ntime.sleep(2)",
    dialect="B65",
    contains=["SLEEP 2"],
    absent=["FOR "],
)

# ── continue ─────────────────────────────────────────────────────────────
check_all(
    "continue in while",
    """
    i = 0
    while i < 6:
        i += 1
        if i == 3:
            continue
        print(i)
""",
)
check_all(
    "continue in for",
    """
    for i in range(6):
        if i == 2:
            continue
        print(i)
""",
)
check_all(
    "continue and break together",
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
check_all("continue outside loop rejected", "continue", should_fail=True)

# ── Chained comparisons ──────────────────────────────────────────────────
check_all(
    "chained comparison",
    "x = 5\nif 1 < x < 10:\n print('mid')",
    contains=["(1 < A) AND (A < 10)"],
)
check_all("triple chain", "x = 5\nif 0 <= x <= 9 <= 20:\n print('y')", contains=["AND"])
check_all(
    "simple comparison unchanged",
    "x = 5\nif x > 1:\n print('y')",
    contains=["A > 1"],
    absent=["AND"],
)

# ── INSTR (BASIC 65 / 7.0 only) ──────────────────────────────────────────
for d in ("B65", "B70"):
    check(
        "find to INSTR",
        's = "hello"\nn = s.find("l")',
        dialect=d,
        contains=['INSTR(A$, "l") - 1'],
    )
    check(
        "in to INSTR",
        's = "hello"\nif "ell" in s:\n print("y")',
        dialect=d,
        contains=['INSTR(A$, "ell") > 0'],
    )
    check(
        "not in to INSTR",
        's = "hi"\nif "z" not in s:\n print("y")',
        dialect=d,
        contains=['INSTR(A$, "z") = 0'],
    )
    check("hex to HEX$", "x = hex(255)", dialect=d, contains=["HEX$(255)"])
    check(
        "int base 16 to DEC",
        'n = "FF"\nx = int(n, 16)',
        dialect=d,
        contains=["DEC(A$)"],
    )
check(
    "find rejected on B20",
    's = "hello"\nn = s.find("l")',
    dialect="B20",
    should_fail=True,
)
check(
    "in rejected on B20",
    's = "hi"\nif "h" in s:\n print(1)',
    dialect="B20",
    should_fail=True,
)
check("hex rejected on B20", "x = hex(255)", dialect="B20", should_fail=True)
check_all("int with base 8 rejected", 'n = "77"\nx = int(n, 8)', should_fail=True)


def check_eq(name, actual, expected):
    global PASS, FAIL
    if actual == expected:
        print(f"PASS {name}")
        PASS += 1
    else:
        print(f"FAIL {name}: {actual!r} != {expected!r}")
        FAIL += 1


# ─────────────────────────────────────────────────────────────────────────────
# Operator precedence survives the trip
# ─────────────────────────────────────────────────────────────────────────────
check_all("parenthesised sum times", "x = (1 + 2) * 3", contains=["(1 + 2) * 3"])
check_all(
    "nested subtraction", "a=1\nb=2\nc=3\nx = a - (b - c)", contains=["A - (B - C)"]
)
check_all(
    "left-nested needs no parens",
    "a=1\nb=2\nc=3\nx = a - b - c",
    contains=["A - B - C"],
    absent=["("],
)
check_all("power of a sum", "x = 2 ** (3 + 1)", contains=["2 ^ (3 + 1)"])
check_all("power is right-associative", "x = 2 ** 3 ** 2", contains=["2 ^ (3 ^ 2)"])
check_all("negative base", "x = (-2) ** 2", contains=["(-2) ^ 2"])
check_all(
    "floor division of a sum",
    "x=7\ny=1\nz = x // (y + 1)",
    contains=["INT(A / (B + 1))"],
)
check_all(
    "aug mult binds the right side",
    "x=2\ny=3\nx *= y + 1",
    contains=["A = A * (B + 1)"],
)
check_all(
    "aug sub binds the right side", "x=2\ny=3\nx -= y - 1", contains=["A = A - (B - 1)"]
)
check_all(
    "aug floordiv binds the right side",
    "x=7\ny=1\nx //= y + 1",
    contains=["A = INT(A / (B + 1))"],
)
check(
    "modulo of a sum on B20",
    "x=5\ny = (x + 1) % 3",
    dialect="B20",
    contains=["(A + 1) - INT((A + 1) / 3) * 3"],
)
check(
    "modulo of a sum on B65",
    "x=5\ny = (x + 1) % 3",
    dialect="B65",
    contains=["MOD(A + 1, 3)"],
)
check(
    "modulo times two on B20",
    "x=5\ny = (x % 3) * 2",
    dialect="B20",
    contains=["(A - INT(A / 3) * 3) * 2"],
)
check(
    "modulo times two on B65",
    "x=5\ny = (x % 3) * 2",
    dialect="B65",
    contains=["MOD(A, 3) * 2"],
)
check_all(
    "comparison of a sum", "a=1\nb=2\nif a + b == 3:\n print(1)", contains=["A + B = 3"]
)
check_all(
    "DEF FN body keeps its parens",
    "def f(x):\n return (x + 1) * 2\ny = f(3)",
    contains=["= (A + 1) * 2"],
)

# ─────────────────────────────────────────────────────────────────────────────
# Conditions are proper booleans: BASIC's AND/OR/NOT are bitwise
# ─────────────────────────────────────────────────────────────────────────────
check(
    "while on a bare number tests nonzero",
    "n=3\nwhile n:\n n -= 1",
    dialect="B20",
    contains=["IF NOT (A <> 0) THEN GOTO"],
)
check(
    "while on a bare number on B65",
    "n=3\nwhile n:\n n -= 1",
    dialect="B65",
    contains=["DO WHILE A <> 0"],
)
check_all(
    "and on bare numbers",
    "x=5\ny=2\nif x and y:\n print(1)",
    contains=["(A <> 0) AND (B <> 0)"],
)
check_all("not on a bare number", "x=5\nif not x:\n print(1)", contains=["(A <> 0)"])
check_all(
    "not on a string tests empty", 's=""\nif not s:\n print(1)', contains=['(A$ <> "")']
)
check_all(
    "comparisons need no coercion",
    "x=5\nif x > 3 and x < 9:\n print(1)",
    contains=["(A > 3) AND (A < 9)"],
    absent=["<> 0"],
)
check_all(
    "sum in a condition",
    "x=5\ny=1\nif x + y:\n print(1)",
    contains=["A + B <> 0"],
)
check(
    "while not drops the double negative",
    "d=0\nwhile not d:\n d=1",
    dialect="B20",
    contains=["IF (A <> 0) THEN GOTO"],
    absent=["NOT"],
)
check(
    "if not drops the double negative",
    "x=5\nif not x > 3:\n print(1)",
    dialect="B20",
    contains=["IF (A > 3) THEN GOTO"],
    absent=["NOT"],
)
check_all("assert on a bare number", "x=5\nassert x", contains=["(A <> 0)"])
check_all(
    "while True has no exit test",
    "while True:\n break",
    absent=["IF", "WHILE", "-1", "REM"],
)
check(
    "while True is a bare DO on B65",
    "while True:\n break",
    dialect="B65",
    contains=["10 DO\n", "EXIT", "LOOP"],
)
check(
    "while True continue jumps to the first body line",
    "x=0\nwhile True:\n x += 1\n if x < 3:\n  continue\n break",
    dialect="B20",
    contains=["GOTO 20"],
)
check(
    "B20 while has no REM line",
    "i=0\nwhile i<3:\n i+=1",
    dialect="B20",
    contains=["20 IF NOT (A < 3) THEN GOTO 50", "40 GOTO 20"],
    absent=["REM"],
)

# ─────────────────────────────────────────────────────────────────────────────
# String typing reaches lists, f-strings and % formatting
# ─────────────────────────────────────────────────────────────────────────────
check_all(
    "string list element is a string",
    'names=["a","b"]\nn=names[0]\nprint(n)',
    contains=["A$ = A$(0)"],
    absent=["A = A$(0)"],
)
check_all(
    "string fill list element is a string",
    'names=[""]*3\nnames[0]="x"\nn=names[0]\nprint(n)',
    contains=["A$ = A$(0)"],
)
check_all(
    "string list element in a concatenation",
    'names=["a"]\ns="x" + names[0]\nprint(s)',
    contains=['A$ = "x" + A$(0)'],
)
check_all(
    "fstring with a slice",
    's="hello"\nprint(f"{s[:3]}")',
    contains=["PRINT LEFT$(A$, 3)"],
    absent=["STR$(LEFT$"],
)
check_all(
    "percent with str()",
    'x=5\nprint("%s" % str(x))',
    contains=["PRINT STR$(A)"],
    absent=["STR$(STR$"],
)
check(
    "fstring with upper",
    's="hi"\nprint(f"{s.upper()}")',
    dialect="B65",
    contains=["PRINT UPPER$(A$)"],
    absent=["STR$("],
)
check_all(
    "fstring with a concatenation",
    'a="x"\nprint(f"{a + a}")',
    contains=["PRINT A$ + A$"],
    absent=["STR$("],
)
check_all(
    "newline in a string becomes CHR$(13)",
    'print("a\\nb")',
    contains=['"a" + CHR$(13) + "b"'],
)
check_all("tab in a string rejected", 'print("a\\tb")', should_fail=True)
check_all(
    "slice with a negative end",
    's="hello"\nt=s[1:-1]\nprint(t)',
    contains=["MID$(A$, 2, LEN(A$) - 2)"],
)
check_all(
    "slice with a variable start and negative end",
    's="hello"\ni=1\nt=s[i:-1]\nprint(t)',
    contains=["MID$(A$, A + 1, LEN(A$) - 1 - A)"],
)
check_all("list size must be a literal", "n=3\nxs=[0]*n", should_fail=True)
check_all("repeating a longer list rejected", "xs=[1, 2]*3", should_fail=True)
check_all(
    "augmented assignment to a list rejected", "xs=[0]*3\nxs += 1", should_fail=True
)

# ─────────────────────────────────────────────────────────────────────────────
# Function shapes that are not subroutines are refused, not misread
# ─────────────────────────────────────────────────────────────────────────────
check_all(
    "keyword-only parameter rejected",
    "def f(*, a):\n print(a)\nf(a=1)",
    should_fail=True,
)
check_all(
    "positional-only parameter rejected",
    "def f(a, /):\n print(a)\nf(1)",
    should_fail=True,
)
check_all(
    "decorator rejected", "@staticmethod\ndef f():\n print(1)\nf()", should_fail=True
)
check_all(
    "decorated DEF FN candidate rejected",
    "@staticmethod\ndef f(x):\n return x * 2\ny = f(1)",
    should_fail=True,
)

# ─────────────────────────────────────────────────────────────────────────────
# Dialect syntax
# ─────────────────────────────────────────────────────────────────────────────
for d in ("B65", "B70"):
    check(
        "else follows BEND after a colon",
        "x=1\nif x:\n print(1)\nelse:\n print(2)",
        dialect=d,
        contains=["BEND : ELSE BEGIN"],
        absent=["BEND ELSE"],
    )
    check("xor is a function", "x = 5 ^ 3", dialect=d, contains=["XOR(5, 3)"])
    check("aug xor", "x=5\nx ^= 1", dialect=d, contains=["A = XOR(A, 1)"])
    check("xor as an operand", "x = (5 ^ 3) + 1", dialect=d, contains=["XOR(5, 3) + 1"])
check("xor rejected on B20", "x = 5 ^ 3", dialect="B20", should_fail=True)
check_all("bitwise and", "x = 5 & 3", contains=["(5) AND (3)"])
check_all(
    "bitwise and as an operand", "x = (5 & 3) + 1", contains=["((5) AND (3)) + 1"]
)
check_all("invert", "x=5\ny = ~x", contains=["NOT (A)"])
check(
    "whole seconds use SLEEP on B70",
    "import time\ntime.sleep(2)",
    dialect="B70",
    contains=["SLEEP 2"],
    absent=["FOR "],
)
check(
    "fractional seconds loop on B70",
    "import time\ntime.sleep(0.5)",
    dialect="B70",
    contains=["TO 500"],
    absent=["SLEEP"],
)
check(
    "runtime seconds loop on B70",
    "import time\nn=2\ntime.sleep(n)",
    dialect="B70",
    contains=["(A) * 1000"],
    absent=["SLEEP"],
)

# ─────────────────────────────────────────────────────────────────────────────
# Input, chained assignment, imports, print spacing
# ─────────────────────────────────────────────────────────────────────────────
check_all(
    "int(input()) reads a number",
    'n = int(input("N? "))\nprint(n)',
    contains=['PRINT "N? ";', "INPUT A", "PRINT A"],
    absent=["INPUT A$"],
)
check_all(
    "float(input()) reads a number",
    "n = float(input())\nprint(n)",
    contains=["10 INPUT A\n"],
)
check_all("plain input is a string", "s = input()\nprint(s)", contains=["INPUT A$"])
check_all(
    "int(input()) in an expression still rejected",
    "n = int(input()) + 1",
    should_fail=True,
)
check_all("chained assignment", "a = b = 0\nprint(a + b)", contains=["A = 0", "B = 0"])
check_all(
    "chained assignment of a string",
    'a = b = "x"\nprint(a + b)',
    contains=['A$ = "x"', 'B$ = "x"'],
)
check_all("chained assignment with input rejected", "a = b = input()", should_fail=True)
check_all(
    "chained assignment with a list rejected", "a = b = [0] * 3", should_fail=True
)
check_all(
    "from random import",
    "from random import randint\nx = randint(1, 6)",
    contains=["INT(RND(1) * (6 - 1 + 1)) + 1"],
)
check_all(
    "from random import seed",
    "from random import seed\nseed(3)",
    contains=["RND(-ABS(3))"],
)
check_all("random alias", "import random as r\nx = r.random()", contains=["RND(1)"])
check("time alias", "import time as t\nt.sleep(1)", dialect="B65", contains=["SLEEP 1"])
check(
    "from time import sleep",
    "from time import sleep\nsleep(1)",
    dialect="B65",
    contains=["SLEEP 1"],
)
check_all("sys alias", "import sys as s\ns.exit()", contains=["END"])
check_all("from sys import exit", "from sys import exit\nexit()", contains=["END"])
check_all(
    "from math import sqrt",
    "from math import sqrt as root\nx = root(4)",
    contains=["SQR(4)"],
)
check_all("star import rejected", "from math import *\nx = sqrt(4)", should_fail=True)
check_all(
    "renamed intrinsic rejected",
    "from py2basic_runtime import poke as p\np(1, 2)",
    should_fail=True,
)
check_all(
    "module-qualified intrinsic",
    "import py2basic_runtime\npy2basic_runtime.poke(1, 2)",
    contains=["POKE 1, 2"],
)
check_all(
    "statement in expression rejected",
    "import time\nx = time.sleep(1)",
    should_fail=True,
)
check_all("quit ends the program", "quit()", contains=["END"])
check_all(
    "print separates two strings",
    'print("Hello,", "world")',
    contains=['PRINT "Hello," ; " " ; "world"'],
)
check_all(
    "print string then number",
    'x=5\nprint("x =", x)',
    contains=['PRINT "x =" ; A'],
    absent=['" "'],
)
check_all(
    "print two numbers",
    "x=5\nprint(x, x)",
    contains=["PRINT A ; A"],
    absent=['" "'],
)
check_all(
    "print explicit empty sep",
    'print("a", "b", sep="")',
    contains=['PRINT "a" ; "b"'],
    absent=['" "'],
)
check_all(
    "print explicit sep",
    'print("a", "b", sep="-")',
    contains=['PRINT "a" ; "-" ; "b"'],
)

# ─────────────────────────────────────────────────────────────────────────────
# Constructor
# ─────────────────────────────────────────────────────────────────────────────
check_eq(
    "dialect class and numbering via the constructor",
    Transpiler(Basic65Dialect, start=100, step=5).transpile("x = 1\ny = 2"),
    "100 A = 1\n105 B = 2",
)
check_eq(
    "dialect name via the constructor",
    Transpiler(Basic7Dialect).dialect.dialect_name(),
    "BASIC 7.0",
)
check_eq("default dialect", Transpiler().dialect.dialect_name(), "BASIC 2.0")

print(f"\n{'=' * 50}")
print(f"Results: {PASS} passed, {FAIL} failed")
if FAIL:
    sys.exit(1)
