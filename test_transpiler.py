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
    test("list rejected", "x=[1,2]", should_fail=True, basic65=b65)
    test("dict rejected", "x={'a':1}", should_fail=True, basic65=b65)
    test("class rejected", "class F: pass", should_fail=True, basic65=b65)
    test("try rejected", "try:\n pass\nexcept: pass", should_fail=True, basic65=b65)
    test(
        "continue rejected",
        "i=0\nwhile i<10:\n i+=1\n continue",
        should_fail=True,
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
    ["REM", "IF NOT (A < 10) THEN GOTO", "GOTO"],
    basic65=False,
)

test(
    "B20 break", "i=0\nwhile i<100:\n if i==5:\n  break\n i+=1", ["GOTO"], basic65=False
)

test("B20 assert", "x=5\nassert x>0", ["IF (A > 0) THEN GOTO", "END"], basic65=False)

test(
    "B20 sleep rejected", "import time\ntime.sleep(1)", should_fail=True, basic65=False
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
    ["IF A > 0 THEN BEGIN", "BEND ELSE BEGIN", "BEND"],
    basic65=True,
)

test(
    "B65 elif",
    "x=5\nif x>10:\n print('b')\nelif x>5:\n print('m')\nelse:\n print('s')",
    ["IF A > 10 THEN BEGIN", "BEND ELSE BEGIN", "BEND"],
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
test7("if/else", "x=5\nif x>0:\n print('y')\nelse:\n print('n')", ["BEND ELSE BEGIN"])
test7(
    "elif",
    "x=5\nif x>10:\n print('b')\nelif x>5:\n print('m')\nelse:\n print('s')",
    ["IF A > 10 THEN BEGIN", "BEND ELSE BEGIN"],
)
test7("while", "i=0\nwhile i<10:\n i+=1", ["DO WHILE A < 10", "LOOP"])
test7("break", "i=0\nwhile i<100:\n if i==5:\n  break\n i+=1", ["EXIT"])

# No MOD() like B20
test7("modulo", "x = 7 % 3", ["- INT("])
test7("aug mod", "x=7\nx%=3", ["- INT("])

# No SLEEP like B20
test7("sleep rejected", "import time\ntime.sleep(1)", should_fail=True)

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

print(f"\n{'=' * 50}")
print(f"Results: {PASS} passed, {FAIL} failed")
if FAIL:
    sys.exit(1)
