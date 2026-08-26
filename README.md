# py2basic — Python to BASIC Transpiler

Transpiles a constrained subset of Python into Commodore BASIC source code.

Write simple programs in Python (with modern tooling, syntax highlighting,
and sane variable names), then run them on your system.

Inspired by the idea that you shouldn't have to think in line numbers.

Capable of generating BASIC 2.0 (Commodore 64 - the default), BASIC 7.0 (Commodore 128) and BASIC65 (MEGA65) dialetcs of BASIC.

## Requirements

- Python 3.8+
- No external dependencies (uses Python's built-in `ast` module)

> Note: parsing is done with Python's own `ast` module rather than a parser
> generator, so there is nothing to install. The architecture is visitor-based.

## Installation

Just copy `transpiler.py` somewhere on your PATH or into your project.

```bash
# Optionally make it directly executable
chmod +x transpiler.py
```

## Usage

```bash
python3 transpiler.py input.py
python3 transpiler.py --basic7 input.py
python3 transpiler.py --basic65 input.py -o output.bas
python3 transpiler.py input.py --vars          # show variable name map
python3 transpiler.py input.py --start 100 --step 10
```

## Supported Python Subset

### Variables

```python
x = 42          # numeric variable
name = "hello"  # string variable (type inferred from assignment)
x = 3.14        # float
```

### Output / Input

```python
print("hello")           # -> PRINT "hello"
print(x, y)              # -> PRINT X ; Y
print()                  # -> PRINT (blank line)
print("a", end="")       # -> PRINT "a";      (no newline)
print(x, y, sep="-")     # -> PRINT X ; "-" ; Y
name = input("Name? ")   # -> PRINT "Name? "; : INPUT A$
```

String literals are passed through as written — the transpiler does not
uppercase them. `sep=` and `end=` must be plain string literals, and `end=`
accepts only `""` or `"\n"`.

### Math operators

```python
x + y    # addition
x - y    # subtraction
x * y    # multiplication
x / y    # division
x % y    # modulo    -> MOD(X, Y)
x ** y   # power     -> X ^ Y
x // y   # floor div -> INT(X / Y)
```

### Augmented assignment

```python
x += 1   # -> X = X + 1
x -= 1   # -> X = X - 1
x *= 2   # -> X = X * 2
x %= 3   # -> X = MOD(X, 3)
```

### Comparison and logical operators

```python
x == y   # =
x != y   # <>
x < y    # <
x > y    # >
x <= y   # <=
x >= y   # >=
a and b  # AND
a or b   # OR
not a    # NOT
```

### Control flow

```python
if x > 0:          # IF X > 0 THEN BEGIN
    print("pos")   #   PRINT "POS"
elif x < 0:        # BEND ELSE BEGIN
    print("neg")   #   IF X < 0 THEN BEGIN
else:              #   BEND ELSE BEGIN
    print("zero")  #     PRINT "ZERO"
                   #   BEND
                   # BEND
```

That is the BASIC 65 / 7.0 rendering. BASIC 2.0 has no `BEGIN`/`BEND`, so
the same code becomes `IF NOT (...) THEN GOTO` with jump targets — see the
FizzBuzz example below.

```python
while x < 10:   # DO WHILE X < 10
    x += 1      #   X = X + 1
                # LOOP
```

`DO WHILE ... LOOP` tests the condition at the top, matching Python. (The
`DO ... LOOP WHILE` form would be a do-while and always run the body once.)
On BASIC 2.0 the same loop becomes a `REM` / `IF NOT ... THEN GOTO` pair.

```python
for i in range(5):         # FOR I = 0 TO 4
    print(i)               #   PRINT I
                           # NEXT I

for i in range(1, 11):     # FOR I = 1 TO 10
for i in range(0, 10, 2):  # FOR I = 0 TO 9 STEP 2
for i in range(10, 0, -1): # FOR I = 10 TO 1 STEP -1
```

The `range()` step must be a literal number — BASIC needs to know the loop
direction at transpile time in order to adjust the inclusive `TO` bound the
right way.

When the bounds are not known until runtime, a guard is emitted ahead of the
loop:

```python
for i in range(n):   # IF 0 > N - 1 THEN GOTO <after>
    print(i)         # FOR A = 0 TO N - 1 ...
```

That guard is not optional. Commodore BASIC tests the bound at `NEXT`, so a
bare `FOR I = 0 TO -1` would run its body once where Python's `range(0)` runs
it zero times.

```python
break      # EXIT in a while loop; GOTO past NEXT in a for loop
continue   # jumps to the loop's test (while) or its NEXT (for)
```

Chained comparisons work, and expand to an `AND`:

```python
if 1 < x < 10:    # IF (1 < A) AND (A < 10)
```

### Functions (subroutines)

```python
def greet():        # REM -- greet (at reserved line)
    print("hello")  # PRINT "HELLO"
                    # RETURN

greet()             # GOSUB <line>
```

Functions **cannot** have parameters or return values. Use global variables
to pass data between subroutines — exactly as you would in BASIC.

### Built-in functions

```python
int(x)      # INT(X)
float(x)    # X          (everything is a float already)
str(x)      # STR$(X)
len(s)      # LEN(S$)
abs(x)      # ABS(X)
chr(n)      # CHR$(N)
ord(c)      # ASC(C$)
round(x)    # INT(X + 0.5)
sqrt(x)     # SQR(X)
sin(x)      # SIN(X)     also cos, tan, atan, exp
log(x)      # LOG(X)
log(x, b)   # (LOG(X) / LOG(B))
```

Argument counts are checked, so `int()` is a clear error rather than a crash.

`int()` and `float()` of a **string** become `VAL()`, since `INT("42")` is a
`?TYPE MISMATCH ERROR` on real hardware.

`s.upper()` and `s.lower()` map to `UPPER$()` / `LOWER$()`, which exist only
in BASIC 65 — the other dialects reject them.

### String slicing

```python
s = "hello world"
s[:5]      # LEFT$(S$, 5)
s[-5:]     # RIGHT$(S$, 5)
s[6:11]    # MID$(S$, 7, 5)
s[2:]      # MID$(S$, 3)
s[1]       # MID$(S$, 2, 1)
s[-1]      # MID$(S$, LEN(S$), 1)
s[:-1]     # LEFT$(S$, LEN(S$) - 1)
```

Slice steps (`s[::2]`) are not supported. A negative bound works only in the
`s[-n:]` and `s[:-n]` forms.

### Lists become arrays

```python
xs = [0] * 10   # DIM A(9)   — DIM allocates 0..n, so a 10-element list is A(9)
xs = [1, 2, 3]  # DIM A(2) : A(0) = 1 : A(1) = 2 : A(2) = 3
names = ["", ""]  # DIM A$(1)

xs[3] = 7       # A(3) = 7
xs[3] += 1      # A(3) = A(3) + 1
y = xs[i]       # A(A)
len(xs)         # folded to a literal at transpile time
```

Arrays have a fixed size and cannot be re-assigned, sliced, or passed around
as a whole. Constant indices are range-checked at transpile time.

Note that in Commodore BASIC `A` and `A(0)` are _different_ variables, so an
array may share a letter with a scalar. That is legal and intentional.

### Functions with a parameter — DEF FN

A one-argument function whose body is a single `return` of a numeric
expression becomes a BASIC `DEF FN`:

```python
def square(x):      # DEF FNA(A) = A * A
    return x * x

y = square(4)       # B = FNA(4)
```

Anything else — more parameters, a string result, or more than one
statement — still has to be a zero-argument `GOSUB` subroutine using global
variables.

### BASIC intrinsics

These have no Python equivalent, so they are exposed as plain functions:

```python
poke(53280, 0)      # POKE 53280, 0
x = peek(1024)      # PEEK(1024)
sys_call(49152)     # SYS 49152
wait(1, 2)          # WAIT 1, 2
x = sgn(-7)         # SGN(-7)
k = ""
k = getkey()        # GET A$
stop()              # STOP

data(1, 2, 3)       # DATA 1,2,3
v = read()          # READ A
restore()           # RESTORE

x = pos(0)          # POS(0)
x = fre(0)          # FRE(0)
print(tab(10))      # TAB(10)
```

To run and test the program under CPython before transpiling, import them
from the bundled runtime:

```python
from py2basic_runtime import peek, poke, sgn
```

`py2basic_runtime` backs POKE/PEEK with a dictionary and stubs the rest, so
the control flow of your program is testable locally.

### Anything else — raw BASIC

For dialect commands this transpiler does not model (graphics, sound, disk),
`basic()` passes a literal statement straight through:

```python
basic("CIRCLE 1,160,100,50")   # CIRCLE 1,160,100,50
basic("SOUND 1,4096,60")       # SOUND 1,4096,60
```

Nothing is validated — you are writing BASIC at that point.

### Random numbers

```python
import random
random.seed(7)              # A = RND(-ABS(7))
random.random()             # RND(1)
random.randint(1, 6)        # INT(RND(1) * (6 - 1 + 1)) + 1
random.randrange(10)        # INT(RND(1) * (10))
```

### Substring search (BASIC 65 / 7.0)

```python
s.find("l")     # INSTR(S$, "l") - 1     — same 0-based / -1 convention
"ell" in s      # INSTR(S$, "ell") > 0
"z" not in s    # INSTR(S$, "z") = 0
hex(255)        # HEX$(255)              — note: no 0x prefix, fixed width
int("FF", 16)   # DEC("FF")
```

BASIC 2.0 has none of these and rejects them with a clear message.

### Standard library (limited)

```python
import time
time.sleep(1.5)   # SLEEP 1.5 on BASIC 65; a FOR/NEXT delay loop elsewhere

import sys
sys.exit()        # END

import math
math.sqrt(16)     # SQR(16)
```

`import math as m` works too — `m.sqrt(x)` resolves the same way.

### Comments

```python
# This becomes a REM statement
```

### assert

```python
assert x > 0           # IF (X > 0) THEN GOTO <past the check>
                       # PRINT "ASSERTION FAILED"
                       # END
assert x > 0, "bad x"  # ... PRINT "bad x" ...
```

## Variable Names

BASIC 65 variable names are very short (single letter, or letter+digit).
The transpiler maintains a symbol table that maps Python names to BASIC names:

| Python name | BASIC 65 name |
| ----------- | ------------- |
| `counter`   | `A`           |
| `total`     | `B`           |
| `name`      | `A$`          |
| `greeting`  | `B$`          |

Use `--vars` to see the full mapping:

```
$ python3 transpiler.py myprog.py --vars

10 A = 0
...

Variable map:
  counter              -> A
  total                -> B
  name                 -> A$
```

Maximum 286 numeric variables and 286 string variables.

Whether a name is numeric or a string is inferred from what is assigned to
it, following concatenations and f-strings through to a fixpoint:

```python
name = "world"                 # A$
greeting = "hello " + name     # B$  — inferred from the concatenation
count = 3                      # A
```

A name used for both is rejected, because a BASIC variable cannot change
type:

```python
v = 1
v = "text"   # error: 'v' is assigned both string and numeric values
```

## Line Numbers

`--start` and `--step` control numbering. `--step` must be at least 1, and
the transpiler stops with an error rather than emitting a line number above
Commodore's limit of 63999.

## What's NOT Supported

The transpiler will give you a clear error message for any of these:

- Function parameters or return values
- Tuples, dicts, sets (lists are supported — see Arrays)
- Classes
- Lambda expressions
- List/dict/set comprehensions
- `try`/`except`/`finally`
- `continue` in loops
- `with` statements
- `import` (except `time`, `sys`, `math`)
- `global` / `nonlocal`
- Multiple assignment targets (`a, b = 1, 2`)
- Chained comparisons (`1 < x < 10`)
- Ternary expressions (`a if c else b`)
- Nested or conditional function definitions
- A `range()` step that is not a literal number
- Reusing one variable for both strings and numbers
- Slice steps, list slicing, and negative list indices
- Resizing a list, or using one as a value
- `min()` / `max()` — no BASIC equivalent

f-strings and `%` formatting **are** supported, minus format specs:

```python
name = "world"
age = 7
print(f"hi {name}, age {age}")   # PRINT "hi " + A$ + ", age " + STR$(B)
print("hi %s, age %d" % (name, age))
print(f"{x:.2f}")                # rejected — format specs are not supported
```

## Example

Input (`fizzbuzz.py`):

```python
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
```

Output (BASIC 2.0, the default dialect):

```
10 A = 1
20 REM
30 IF NOT (A <= 20) THEN GOTO 180
40 B = INT(A / 3) * 3
50 C = INT(A / 5) * 5
60 IF NOT ((B = A) AND (C = A)) THEN GOTO 90
70 PRINT "FIZZBUZZ"
80 GOTO 160
90 IF NOT (B = A) THEN GOTO 120
100 PRINT "FIZZ"
110 GOTO 160
120 IF NOT (C = A) THEN GOTO 150
130 PRINT "BUZZ"
140 GOTO 160
150 PRINT A
160 A = A + 1
170 GOTO 20
180 END
```

## License

GPL v2
