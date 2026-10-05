# basic2py — Commodore BASIC to Python Transpiler

Best-effort transpiler from Commodore BASIC (2.0, 7.0, 65) to Python.

The companion to [py2basic](../transpiler.py) — goes the other direction.

## Requirements

Python 3.8+, no external dependencies.

## Usage

```bash
python3 basic2py.py program.bas
python3 basic2py.py program.bas -o program.py
python3 basic2py.py program.bas --check   # exit non-zero if the output
                                          # does not parse as Python
```

The generated Python is always parsed before it is handed back. If it does
not parse you get a warning on stderr naming the line, and `--check` turns
that warning into a non-zero exit status.

## Tests

```bash
python3 test_basic2py.py
```

Covers the conversions below plus Python → BASIC → Python round trips
through all three dialects, comparing program _output_ rather than source.

## What it handles

| BASIC construct                  | Python output                                    |
| -------------------------------- | ------------------------------------------------ |
| `FOR I = 1 TO 10` / `NEXT I`     | `for i in range(1, 11):`                         |
| `DO WHILE cond` / `LOOP`         | `while cond:`                                    |
| `DO UNTIL cond` / `LOOP`         | `while not cond:`                                |
| `DO` / `LOOP WHILE cond`         | `while True:` + trailing `break` (bottom-tested) |
| `EXIT`                           | `break`                                          |
| `IF NOT (cond) THEN GOTO n`      | `if cond:` / `if/else`                           |
| `IF cond THEN GOTO n`            | `if not cond:`, or `break` / `continue`          |
| `IF (c) THEN GOTO` + PRINT + END | `assert c, "message"`                            |
| `GOTO` back to an earlier line   | `while cond:` / `while True:`                    |
| `IF cond THEN BEGIN` / `BEND`    | `if cond:` / `if/else`                           |
| `BEND : ELSE BEGIN`              | `else:`                                          |
| `GOSUB n` / `RETURN`             | `def func():` / `func()`                         |
| `PRINT a ; b`                    | `print(a, b)`                                    |
| `INPUT a_str`                    | `a_str = input()`                                |
| `INPUT a` (numeric)              | `a = float(input())`                             |
| `INPUT "p"; a, b`                | one `input()` per variable                       |
| `REM text`                       | `# text`                                         |
| `END`                            | `sys.exit()`                                     |
| `SLEEP n`                        | `time.sleep(n)`                                  |
| `MOD(a, b)`                      | `a % b`                                          |
| `STR$(x)`                        | `str(x)`                                         |
| `CHR$(x)`                        | `chr(x)`                                         |
| `ASC(x)`                         | `ord(x)`                                         |
| `LEN(x)`                         | `len(x)`                                         |
| `INT(x)`                         | `int(x)`                                         |
| `ABS(x)`                         | `abs(x)`                                         |
| `SQR(x)`                         | `math.sqrt(x)`                                   |
| `SIN/COS/TAN/ATN/LOG/EXP`        | `math.sin()` etc                                 |
| `x ^ y`                          | `x ** y`                                         |
| `x <> y`                         | `x != y`                                         |
| `CHR$(34)` concatenation         | Restored to an escaped `\"` in the string        |
| `CHR$(13)` concatenation         | Restored to a `\n` in the string                 |
| `RETURN` before the end          | `return`                                         |
| `A = 1 : B = 2`                  | Split into separate statements                   |
| `NEXT` with no variable          | Closes the innermost `FOR`                       |
| `LEFT$(s,n)` / `RIGHT$(s,n)`     | `s[:n]` / `s[-n:]`                               |
| `MID$(s,a,b)`                    | `s[a-1:a-1+b]`                                   |
| `VAL(s)`                         | `float(s)`                                       |
| `INSTR(a,b)`                     | `a.find(b) + 1`                                  |
| `HEX$(x)` / `DEC(s)`             | `hex(x)` / `int(s, 16)`                          |
| `RND`, `SGN`, `PEEK`, `FRE`      | runtime shims — see below                        |
| `POS`, `TAB`, `SPC`              | runtime shims — see below                        |
| `DIM a(n)`                       | `a_arr = [0] * (n + 1)`                          |
| `a(i) = v`                       | `a_arr[i] = v`                                   |
| `DATA` / `READ` / `RESTORE`      | `_DATA` list plus `_read()` / `_restore()`       |
| `DEF FNa(x) = expr`              | `def fna(x): return expr`                        |
| `ON x GOTO` / `ON x GOSUB`       | `if`/`elif` ladder                               |
| `IF c THEN a : ELSE b`           | `if c: a` / `else: b`                            |
| `GETKEY v$`                      | `v_str = input()[:1]`                            |
| `STOP`                           | `sys.exit()`                                     |
| `POKE` / `SYS` / `WAIT`          | `# TODO` markers                                 |

Variable names are expanded:

- `A` → `a`
- `A$` → `a_str`
- `A0` → `a0`
- `A0$` → `a0_str`
- `A(n)` → `a_arr[n]`
- `A$(n)` → `a_str_arr[n]`

Arrays get the `_arr` suffix on purpose: in Commodore BASIC `A` and `A(0)`
are different variables, and mapping both to `a` silently fuses them — the
recovered program then fails with a `TypeError`.

Function names are recovered from `REM -- name` comments
(as generated by py2basic). Unnamed subroutines become `sub_NNN`.

## Fidelity notes

- String literals keep their case; only code outside the quotes is
  uppercased.
- `FOR I = 10 TO 1 STEP -1` becomes `range(10, 0, -1)` — the inclusive `TO`
  bound is adjusted according to the sign of the step.
- `DO ... LOOP WHILE` is bottom-tested, so it is rendered as
  `while True:` with a trailing conditional `break` rather than a plain
  `while`, which would change the trip count.
- Lines with no line number are skipped with a warning on stderr.
- `GOTO`s that jump to the enclosing loop's `NEXT`/`LOOP`, or past its end,
  are recovered as `continue` and `break`.
- A line that a later `GOTO` jumps back to is the top of a loop. If it is an
  `IF ... THEN GOTO` whose target is the line after that `GOTO`, the loop is
  `while cond:`; otherwise it is `while True:`.
- Function arguments are split by matching parentheses, so
  `LEFT$(S$, LEN(S$) - 1)` converts correctly however deeply it nests.
- Nothing inside a string literal is rewritten; `"A AND B"` stays as it is.
- Only the runtime shims a program actually uses are emitted.

## Runtime shims

Some BASIC functions have no Python counterpart, so basic2py emits a small
definition alongside the translated code:

| Shim                  | Behaviour                             |
| --------------------- | ------------------------------------- |
| `_rnd(x)`             | `random.random()`                     |
| `_sgn(x)`             | sign of x                             |
| `_peek(addr)`         | always 0 — there is no memory to read |
| `_dec(s)`             | `int(s, 16)`                          |
| `_fre(x)` / `_pos(x)` | always 0                              |
| `_tab(n)` / `_spc(n)` | n spaces                              |

## What it can't handle

- Arbitrary `GOTO` jumps that don't match known patterns
  → preserved as `# TODO: GOTO n — unresolved jump`
- The branch targets of `ON x GOTO` (the ladder is recovered, but each
  destination is still an unresolved jump)
- `LOAD`, `SAVE`, `OPEN`, `CLOSE`, `PRINT#`, `INPUT#` — file and device I/O
- Graphics and sound commands
- `POKE` / `SYS` / `WAIT` address hardware that does not exist here, so they
  become TODO markers rather than silent no-ops
- Any other unrecognised `NAME(` call is collected into a single
  `# TODO: unconverted BASIC calls: ...` line, so nothing reaches the output
  looking converted when it is not

## Honest expectations

Programs written with **py2basic** round-trip very cleanly — the
transpiler generates predictable patterns that basic2py recognises.

Real-world BASIC programs with creative use of GOTOs will produce
output with TODO comments marking the unresolved jumps. That's
expected and honest — use the TODO markers as a guide for manual
cleanup.

## Example round-trip

```
Python → py2basic → BASIC 2.0 → basic2py → Python
```

FizzBuzz goes in, FizzBuzz comes out. Variable names change
(i→a, m3→b, m5→c) but the logic is intact and the output
runs correctly.

## License

GPL v2
