#!/usr/bin/env python3
"""
py2basic test suite — tests both BASIC 2.0 and BASIC 65 dialects
"""

import os
import sys

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

test("B65 while", "i=0\nwhile i<10:\n i+=1", ["DO", "LOOP WHILE A < 10"], basic65=True)

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
test("fizzbuzz", FIZZBUZZ, ["DO", "LOOP WHILE", "BEGIN", "BEND"], basic65=True)


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
test7("while", "i=0\nwhile i<10:\n i+=1", ["DO", "LOOP WHILE A < 10"])
test7("break", "i=0\nwhile i<100:\n if i==5:\n  break\n i+=1", ["EXIT"])

# No MOD() like B20
test7("modulo", "x = 7 % 3", ["- INT("])
test7("aug mod", "x=7\nx%=3", ["- INT("])

# No SLEEP like B20
test7("sleep rejected", "import time\ntime.sleep(1)", should_fail=True)

# Shared basics still work
test7("for loop", "for i in range(5):\n print(i)", ["FOR", "= 0 TO 4", "NEXT"])
test7("function", "def f():\n print('hi')\nf()", ["GOSUB", "RETURN"])
test7("fizzbuzz", FIZZBUZZ, ["DO", "LOOP WHILE", "BEGIN", "BEND"])

print(f"\n{'=' * 50}")
print(f"Results: {PASS} passed, {FAIL} failed")
if FAIL:
    sys.exit(1)
