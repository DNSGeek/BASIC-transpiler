# py2basic — Python to BASIC Transpiler

Transpiles a constrained subset of Python into Commodore BASIC source code.

Write simple programs in Python (with modern tooling, syntax highlighting,
and sane variable names), then run them on your system.

Inspired by the idea that you shouldn't have to think in line numbers.

Capable of generating BASIC 2.0 (Commodore 64 - the default), BASIC 7.0 (Commodore 128) and BASIC65 (MEGA65) dialetcs of BASIC.

## Requirements

- Python 3.8+
- No external dependencies (uses Python's built-in `ast` module)

> Note: Despite the name, this transpiler does **not** use ANTLR4. It uses
> Python's own `ast` module to parse the input, which is simpler and requires
> no additional tools. The architecture is visitor-based in the same spirit.

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
print("hello")           # -> PRINT "HELLO"
print(x, y)              # -> PRINT X ; Y
print()                  # -> PRINT (blank line)
name = input("Name? ")   # -> PRINT "Name? " : INPUT A$
```

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

```python
while x < 10:   # DO
    x += 1      #   X = X + 1
                # LOOP WHILE X < 10
```

```python
for i in range(5):         # FOR I = 0 TO 4
    print(i)               #   PRINT I
                           # NEXT I

for i in range(1, 11):     # FOR I = 1 TO 10
for i in range(0, 10, 2):  # FOR I = 0 TO 9 STEP 2
```

```python
break   # EXIT  (exits a DO/LOOP)
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
str(x)      # STR$(X)
len(s)      # LEN(S$)
abs(x)      # ABS(X)
chr(n)      # CHR$(N)
ord(c)      # ASC(C$)
round(x)    # INT(X + 0.5)
```

### Standard library (limited)

```python
import time
time.sleep(1.5)   # SLEEP 1.5

import sys
sys.exit()        # END
```

### Comments

```python
# This becomes a REM statement
```

### assert

```python
assert x > 0           # IF NOT (X > 0) THEN BEGIN : PRINT "Assertion failed" : END : BEND
assert x > 0, "bad x"  # IF NOT (X > 0) THEN BEGIN : PRINT "BAD X" : END : BEND
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
- f-strings (use `str(x)` concatenation instead)
- Nested functions

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

Output:

```
10 A = 1
20 DO
30 B = INT(A / 3) * 3
40 C = INT(A / 5) * 5
50 IF (B = A) AND (C = A) THEN BEGIN
60 PRINT "FIZZBUZZ"
70 BEND ELSE BEGIN
80 IF B = A THEN BEGIN
90 PRINT "FIZZ"
100 BEND ELSE BEGIN
110 IF C = A THEN BEGIN
120 PRINT "BUZZ"
130 BEND ELSE BEGIN
140 PRINT A
150 BEND
160 BEND
170 BEND
180 A = A + 1
190 LOOP WHILE A <= 20
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
