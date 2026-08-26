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
break   # EXIT in a while loop; GOTO past NEXT in a for loop
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

`s.upper()` and `s.lower()` map to `UPPER$()` / `LOWER$()`, which exist only
in BASIC 65 — the other dialects reject them.

### Standard library (limited)

```python
import time
time.sleep(1.5)   # SLEEP 1.5   (BASIC 65 only)

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
- Lists, tuples, dicts, sets
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

## Transferring to MEGA65

1. Save the output as a `.bas` file
2. Copy it to your MEGA65's SD card (or a D81 image)
3. On the MEGA65: `IMPORT "FIZZBUZZ.BAS"`
4. `LIST` to verify, `RUN` to execute

## License

MIT — do whatever you want with it.

## Acknowledgements

Built for the MEGA65 community. BASIC 65 dialect reference from the MEGA65
User's Guide and ROM 920413 binary analysis.
