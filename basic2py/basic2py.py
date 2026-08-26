"""
basic2py - Commodore BASIC to Python Transpiler
================================================
Best-effort transpiler from Commodore BASIC (2.0, 7.0, 65) to Python.

Handles:
  - FOR/NEXT loops        -> for i in range()
  - GOSUB/RETURN          -> def functions()
  - IF/GOTO patterns      -> if/elif/else
  - WHILE/DO loops        -> while
  - BEGIN/BEND blocks     -> structured if/else (BASIC 65/7.0)
  - DO/LOOP WHILE         -> while (BASIC 65/7.0)
  - REM                   -> # comments
  - PRINT                 -> print()
  - INPUT                 -> input()
  - Assignment            -> var = expr
  - MOD(a,b)             -> a % b
  - STR$(x)              -> str(x)
  - CHR$(x)              -> chr(x)
  - ASC(x)               -> ord(x)
  - LEN(x)               -> len(x)
  - INT(x)               -> int(x)
  - ABS(x)               -> abs(x)
  - SQR(x)               -> math.sqrt(x)
  - SIN/COS/TAN/ATN      -> math.sin() etc
  - LOG(x)               -> math.log(x)
  - END                  -> sys.exit()
  - SLEEP                -> time.sleep()

Unresolvable GOTOs are preserved as comments with a TODO marker.
Variable names are expanded: A->a, A$->a_str, A0->a0, A0$->a0_str
"""

import ast
import re
import sys
import textwrap
from dataclasses import dataclass
from typing import ClassVar

# ── String-literal-aware helpers ───────────────────────────────────────────────


def upper_outside_strings(text: str) -> str:
    """
    Uppercase BASIC keywords and variable names but leave string literals
    alone. Uppercasing the whole line destroys the case of anything the
    program prints.
    """
    out = []
    in_string = False
    for ch in text:
        if ch == '"':
            in_string = not in_string
            out.append(ch)
        else:
            out.append(ch if in_string else ch.upper())
    return "".join(out)


def split_outside_strings(text: str, sep: str) -> list:
    """Split text on sep, ignoring separators inside string literals."""
    parts = []
    current = []
    in_string = False
    for ch in text:
        if ch == '"':
            in_string = not in_string
            current.append(ch)
        elif ch == sep and not in_string:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


def sub_outside_strings(pattern, repl, text: str) -> str:
    """Apply re.sub only to the parts of text outside string literals."""
    out = []
    for i, chunk in enumerate(re.split(r'("[^"]*")', text)):
        # Odd indices are the captured string literals — leave them be.
        out.append(chunk if i % 2 else re.sub(pattern, repl, chunk))
    return "".join(out)


# ── Data structures ────────────────────────────────────────────────────────────


@dataclass
class BasicLine:
    number: int
    text: str  # statement text, uppercased outside string literals
    original: str  # original as-is


# ── Variable name mapper ───────────────────────────────────────────────────────


class VarMapper:
    """
    Maps BASIC variable names to Python names.
    A     -> a
    A$    -> a_str
    A0    -> a0
    A0$   -> a0_str
    A()   -> a_arr
    A$()  -> a_str_arr

    Arrays get the `_arr` suffix because in Commodore BASIC `A` and `A(0)`
    are *different* variables. Mapping both to `a` silently fuses them, and
    the recovered program then fails with a TypeError.
    """

    def __init__(self):
        self._cache = {}

    def map(self, basic_name: str, is_array: bool = False) -> str:
        key = (basic_name, is_array)
        if key in self._cache:
            return self._cache[key]

        name = basic_name.strip()
        is_string = name.endswith("$")
        if is_string:
            name = name[:-1]

        # Lowercase and clean
        py_name = name.lower()
        if is_string:
            py_name += "_str"
        if is_array:
            py_name += "_arr"

        self._cache[key] = py_name
        return py_name


# ── Expression converter ───────────────────────────────────────────────────────


class ExprConverter:
    """Converts BASIC expressions to Python expressions."""

    #: BASIC function -> Python equivalent, applied as a plain rename.
    SIMPLE_FUNCTIONS: ClassVar[tuple] = (
        (r"STR\$", "str"),
        (r"CHR\$", "chr"),
        (r"HEX\$", "hex"),
        ("ASC", "ord"),
        ("LEN", "len"),
        ("INT", "int"),
        ("ABS", "abs"),
        ("VAL", "float"),
        ("SQR", "math.sqrt"),
        ("SIN", "math.sin"),
        ("COS", "math.cos"),
        ("TAN", "math.tan"),
        ("ATN", "math.atan"),
        ("LOG", "math.log"),
        ("EXP", "math.exp"),
        ("RND", "_rnd"),
        ("SGN", "_sgn"),
        ("PEEK", "_peek"),
        ("DEC", "_dec"),
        ("FRE", "_fre"),
        ("POS", "_pos"),
        ("TAB", "_tab"),
        ("SPC", "_spc"),
    )

    #: Runtime shims emitted when the program uses one of these.
    SHIMS: ClassVar[dict] = {
        "_rnd": "def _rnd(_x=1):\n    return random.random()",
        "_sgn": "def _sgn(x):\n    return (x > 0) - (x < 0)",
        "_peek": "def _peek(_addr):\n    return 0  # TODO: no memory to read",
        "_dec": "def _dec(s):\n    return int(s, 16)",
        "_fre": "def _fre(_x=0):\n    return 0  # TODO: no BASIC heap",
        "_pos": "def _pos(_x=0):\n    return 0  # TODO: cursor column unknown",
        "_tab": "def _tab(n):\n    return ' ' * int(n)",
        "_spc": "def _spc(n):\n    return ' ' * int(n)",
    }

    #: CHR$(34) — the BASIC idiom for a double quote inside a string.
    _CHR34 = r"(?:CHR\$|chr)\s*\(\s*34\s*\)"

    def __init__(self, var_mapper: VarMapper):
        self.vm = var_mapper
        #: BASIC names known to be arrays, from DIM. A(1) is a subscript,
        #: not a call, so the variable mapper has to be told the difference.
        self.arrays = set()

    def _convert_substring(self, expr: str) -> str:
        """LEFT$/RIGHT$/MID$ -> Python slices."""
        expr = re.sub(
            r"\bLEFT\$\s*\(([^,]+),\s*([^)]+)\)",
            lambda m: f"({self.convert(m.group(1))})[:{self.convert(m.group(2))}]",
            expr,
            flags=re.IGNORECASE,
        )
        expr = re.sub(
            r"\bRIGHT\$\s*\(([^,]+),\s*([^)]+)\)",
            lambda m: f"({self.convert(m.group(1))})[-({self.convert(m.group(2))}):]",
            expr,
            flags=re.IGNORECASE,
        )
        # MID$(s, start, len) and the two-argument MID$(s, start).
        expr = re.sub(
            r"\bMID\$\s*\(([^,]+),\s*([^,)]+),\s*([^)]+)\)",
            lambda m: (
                f"({self.convert(m.group(1))})"
                f"[({self.convert(m.group(2))}) - 1:"
                f"({self.convert(m.group(2))}) - 1 + ({self.convert(m.group(3))})]"
            ),
            expr,
            flags=re.IGNORECASE,
        )
        expr = re.sub(
            r"\bMID\$\s*\(([^,]+),\s*([^)]+)\)",
            lambda m: (
                f"({self.convert(m.group(1))})" f"[({self.convert(m.group(2))}) - 1:]"
            ),
            expr,
            flags=re.IGNORECASE,
        )
        return expr

    def _convert_chr34(self, expr: str) -> str:
        """
        Fold CHR$(34) concatenation back into a single Python string literal.

        The quote has to be *escaped* on the way in. Splicing a bare `"` in
        produces `print("say "hi")`, which is a SyntaxError rather than a
        program.

          "say " + CHR$(34) + "hi" + CHR$(34)  ->  "say \\"hi\\""
        """
        # Between two literals: "a" + CHR$(34) + "b"  ->  "a\"b"
        expr = re.sub(
            rf'"\s*\+\s*{self._CHR34}\s*\+\s*"',
            lambda _m: '\\"',
            expr,
            flags=re.IGNORECASE,
        )
        # Trailing: "a" + CHR$(34)  ->  "a\""
        expr = re.sub(
            rf'"\s*\+\s*{self._CHR34}',
            lambda _m: '\\""',
            expr,
            flags=re.IGNORECASE,
        )
        # Leading: CHR$(34) + "a"  ->  "\"a"
        expr = re.sub(
            rf'{self._CHR34}\s*\+\s*"',
            lambda _m: '"\\"',
            expr,
            flags=re.IGNORECASE,
        )
        return expr

    def _map_vars_in_expr(self, expr: str) -> str:
        """Map BASIC variable names to Python names, avoiding function names."""
        # Protect string literals from variable substitution
        strings = []

        def save_string(m):
            strings.append(m.group(0))
            return f"__STR{len(strings) - 1}__"

        expr = re.sub(r'"[^"]*"', save_string, expr)

        # Map string vars first (A$ before A)
        expr = re.sub(r"\b([A-Z][0-9]?)\$", lambda m: self.vm.map(m.group(0)), expr)
        # Map numeric vars — but not if followed by ( (function call)
        expr = re.sub(
            r"\b([A-Z][0-9]?)\b(?!\s*\()",
            lambda m: (
                self.vm.map(m.group(0))
                if m.group(0)
                not in (
                    "AND",
                    "OR",
                    "NOT",
                    "TO",
                    "STEP",
                    "THEN",
                    "GOTO",
                    "GOSUB",
                    "FOR",
                    "NEXT",
                    "IF",
                    "REM",
                    "END",
                    "MOD",
                )
                else m.group(0)
            ),
            expr,
        )

        # Restore string literals
        for i, s in enumerate(strings):
            expr = expr.replace(f"__STR{i}__", s)

        return expr

    def _convert_arrays(self, expr: str) -> str:
        """A(3) -> a[3] for every name that appeared in a DIM."""
        if not self.arrays:
            return expr
        names = sorted(self.arrays, key=len, reverse=True)
        pattern = r"\b(" + "|".join(re.escape(n) for n in names) + r")\s*\(([^()]*)\)"
        return re.sub(
            pattern,
            lambda m: (
                f"{self.vm.map(m.group(1), is_array=True)}"
                f"[{self.convert(m.group(2))}]"
            ),
            expr,
            flags=re.IGNORECASE,
        )

    def convert(self, expr: str) -> str:
        expr = expr.strip()

        # String concatenation: " + CHR$(34) + " patterns
        expr = self._convert_chr34(expr)

        # Two-argument string functions, before the simple renames so the
        # argument split still sees the original commas.
        expr = self._convert_substring(expr)

        # Simple renames. Order matters — most specific first.
        for basic_fn, py_fn in self.SIMPLE_FUNCTIONS:
            expr = re.sub(rf"\b{basic_fn}\s*\(", f"{py_fn}(", expr, flags=re.IGNORECASE)
        expr = re.sub(
            r"\bUPPER\$\s*\(([^)]+)\)", r"(\1).upper()", expr, flags=re.IGNORECASE
        )
        expr = re.sub(
            r"\bLOWER\$\s*\(([^)]+)\)", r"(\1).lower()", expr, flags=re.IGNORECASE
        )
        # INSTR(hay, needle) is 1-based and 0 when absent; .find() is
        # 0-based and -1, so the offset lines up exactly.
        expr = re.sub(
            r"\bINSTR\s*\(([^,]+),\s*([^)]+)\)",
            lambda m: (
                f"({self.convert(m.group(1))}).find" f"({self.convert(m.group(2))}) + 1"
            ),
            expr,
            flags=re.IGNORECASE,
        )

        # MOD(a, b) -> (a) % (b)
        expr = re.sub(
            r"\bMOD\s*\(([^,]+),\s*([^)]+)\)",
            lambda m: f"({self.convert(m.group(1))}) % ({self.convert(m.group(2))})",
            expr,
            flags=re.IGNORECASE,
        )

        # Operators
        expr = re.sub(r"\^", "**", expr)
        expr = re.sub(r"\bAND\b", "and", expr, flags=re.IGNORECASE)
        expr = re.sub(r"\bOR\b", "or", expr, flags=re.IGNORECASE)
        expr = re.sub(r"\bNOT\b", "not", expr, flags=re.IGNORECASE)
        expr = re.sub(r"<>", "!=", expr)

        # Array subscripts, before variable mapping — the mapper skips any
        # name followed by "(", assuming it is a function call.
        expr = self._convert_arrays(expr)

        # Map variable names (after function conversion to avoid clobbering)
        expr = self._map_vars_in_expr(expr)

        # Clean up double negation from NOT NOT patterns
        expr = re.sub(r"\bnot\s+not\b", "", expr)

        return expr.strip()

    def _balanced(self, s: str) -> bool:
        """Check if parentheses are balanced."""
        depth = 0
        in_str = False
        for ch in s:
            if ch == '"':
                in_str = not in_str
            if not in_str:
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth < 0:
                        return False
        return depth == 0

    def convert_condition(self, cond: str) -> str:
        """Convert a BASIC condition, handling = as == in comparisons."""
        cond = cond.strip()
        # Strip outer parens if balanced
        if cond.startswith("(") and cond.endswith(")") and self._balanced(cond[1:-1]):
            cond = cond[1:-1].strip()
        result = self.convert(cond)
        # Replace bare = with == — but only outside string literals, or a
        # comparison against "x=y" silently becomes "x==y".
        result = sub_outside_strings(r"(?<![!<>])=(?!=)", "==", result)
        result = sub_outside_strings(r"===", "==", result)
        return result


# ── BASIC parser ───────────────────────────────────────────────────────────────


class BasicParser:
    """Parse BASIC source into a list of BasicLine objects."""

    @staticmethod
    def statements(text: str) -> list:
        """
        Split one BASIC line into its colon-separated statements.

        REM and DATA swallow the rest of the line, and in an unstructured
        `IF ... THEN ...` everything after THEN belongs to the THEN clause —
        so those are left whole.
        """
        upper = upper_outside_strings(text)
        if re.match(r"^\s*(REM|DATA)\b", upper):
            return [text]
        if re.match(r"^\s*IF\b", upper) and not re.search(r"\bTHEN\s+BEGIN\s*$", upper):
            return [text]
        return split_outside_strings(text, ":")

    def parse(self, source: str) -> list:
        lines = []
        skipped = 0
        for raw_line in source.strip().splitlines():
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            # Match line number
            m = re.match(r"^(\d+)\s*(.*)", raw_line)
            if not m:
                skipped += 1
                print(
                    f"Warning: ignoring line without a line number: {raw_line!r}",
                    file=sys.stderr,
                )
                continue
            num = int(m.group(1))
            lines.extend(
                BasicLine(
                    number=num,
                    text=upper_outside_strings(stmt),
                    original=stmt,
                )
                for stmt in self.statements(m.group(2).strip())
            )
        if skipped:
            print(f"Warning: {skipped} line(s) had no line number.", file=sys.stderr)
        lines.sort(key=lambda ln: ln.number)
        return lines


# ── Control flow analyser ──────────────────────────────────────────────────────


class CFGAnalyser:
    """
    Identifies subroutine boundaries and GOTO targets.
    """

    def _scan(self):
        for line in self.lines:
            text = line.text

            # Collect all GOTO targets
            for m in re.finditer(r"\bGOTO\s+(\d+)", text):
                self.goto_targets.add(int(m.group(1)))
            for m in re.finditer(r"\bTHEN\s+(\d+)", text):
                self.goto_targets.add(int(m.group(1)))

            # Collect GOSUB targets
            for m in re.finditer(r"\bGOSUB\s+(\d+)", text):
                self.gosub_targets.add(int(m.group(1)))

            # Find function names from REM -- name patterns
            m = re.match(r"^REM\s+--\s+(.+)$", text)
            if m:
                self.func_names[line.number] = (
                    m.group(1).strip().lower().replace(" ", "_")
                )

    def __init__(self, lines: list):
        self.lines = lines
        self.goto_targets = set()
        self.gosub_targets = set()
        self.func_names = {}  # line_number -> name

        self._scan()

    def is_subroutine_start(self, line_num: int) -> bool:
        return line_num in self.gosub_targets

    def func_name_at(self, line_num: int) -> str | None:
        return self.func_names.get(line_num)


# ── Main transpiler ────────────────────────────────────────────────────────────


class Transpiler:

    #: Names that may legitimately survive expression conversion.
    KNOWN_NAMES = frozenset(
        {
            "abs",
            "chr",
            "float",
            "hex",
            "int",
            "len",
            "ord",
            "str",
            "input",
            "print",
            "range",
            "and",
            "or",
            "not",
            "if",
            "else",
            "for",
            "in",
            "while",
            "break",
            "def",
            "return",
            "end",
            "math",
            "time",
            "sys",
            "random",
            "_rnd",
            "_sgn",
            "_peek",
            "_dec",
            "_fre",
            "_pos",
            "_tab",
            "_spc",
            "_read",
            "_restore",
        }
    )

    def __init__(self):
        self.vm = VarMapper()
        self.ec = ExprConverter(self.vm)
        self.output_lines = []
        self._indent = 0
        self._needs_sys = False
        self._needs_data = False
        self._data_items = []
        self._unknown_calls = set()
        #: Jump targets that mean break/continue inside the enclosing loop.
        self._loop_stack = []

    def _flag_unknown_calls(self, text: str):
        """
        Warn about calls this converter did not recognise.

        Without this a BASIC function with no mapping survives as a bare
        name — `a = PEEK(1024)` parses cleanly and then raises NameError at
        runtime, which is worse than an honest TODO.
        """
        for m in re.finditer(r"\b([A-Za-z_][A-Za-z_0-9.]*)\s*\(", text):
            name = m.group(1)
            if name in self.KNOWN_NAMES or name.startswith("math."):
                continue
            if name.split(".")[0] in self.KNOWN_NAMES:
                continue
            self._unknown_calls.add(name)

    def _emit(self, text: str = ""):
        prefix = "    " * self._indent
        stripped = text.lstrip()
        if not stripped.startswith("#"):
            self._flag_unknown_calls(text)
        self.output_lines.append(prefix + text)

    def _loop_keyword(self, target):
        """`break` or `continue` if target is a landmark of the current loop."""
        if not self._loop_stack:
            return None
        # Only the innermost loop can be left with a bare GOTO.
        frame = self._loop_stack[-1]
        if target == frame.get("continue"):
            return "continue"
        if target == frame.get("break"):
            return "break"
        return None

    def _open_block(self):
        """Mark the start of an indented block. Pair with _close_block()."""
        self._indent += 1
        return len(self.output_lines)

    def _close_block(self, mark):
        """
        Close an indented block, adding `pass` if nothing substantive landed
        in it. A block holding only TODO comments is an IndentationError.
        """
        substantive = any(
            out.strip() and not out.strip().startswith("#")
            for out in self.output_lines[mark:]
        )
        if not substantive:
            self._emit("pass")
        self._indent -= 1

    def _emit_comment(self, text: str):
        self._emit(f"# {text}")

    def _emit_todo(self, text: str):
        self._emit(f"# TODO: {text}")

    @staticmethod
    def _split_then_else(stmt: str):
        """Split a single-line THEN clause at a top-level ELSE."""
        parts = split_outside_strings(stmt, ":")
        for index, part in enumerate(parts):
            if re.match(r"^ELSE\b", upper_outside_strings(part)):
                tail = re.sub(r"^ELSE\s*", "", part, count=1, flags=re.IGNORECASE)
                rest = ([tail] if tail.strip() else []) + parts[index + 1 :]
                return parts[:index], rest
        return parts, None

    def _emit_inline(self, statements, line_number, cfg):
        """Emit colon-separated statements as an indented block."""
        block = [
            BasicLine(
                number=line_number,
                text=upper_outside_strings(s),
                original=s,
            )
            for s in statements
            if s.strip()
        ]
        if block:
            self._emit_block(block, cfg, 0)

    def _assign_target(self, target: str) -> str:
        """A READ/INPUT destination, which may be an array element."""
        target = target.strip()
        m = re.match(r"^([A-Z][0-9]?\$?)\s*\((.+)\)$", target, re.IGNORECASE)
        if m:
            name = self.vm.map(m.group(1), is_array=True)
            return f"{name}[{self.ec.convert(m.group(2))}]"
        return self.vm.map(target)

    @staticmethod
    def _split_data(items: str) -> list:
        """DATA operands: bare words are strings, numbers stay numbers."""
        out = []
        for raw in split_outside_strings(items, ","):
            raw = raw.strip()
            if raw.startswith('"') and raw.endswith('"'):
                out.append(raw)
                continue
            try:
                float(raw)
            except ValueError:
                out.append(f'"{raw}"')
            else:
                out.append(raw)
        return out

    def _emit_dim(self, spec: str):
        """DIM a(n) -> a = [0] * (n + 1); BASIC subscripts run 0..n."""
        for decl in split_outside_strings(spec, ","):
            m = re.match(r"^([A-Z][0-9]?\$?)\s*\((.+)\)$", decl.strip(), re.IGNORECASE)
            if not m:
                self._emit_todo(f"Unrecognised DIM: {decl.strip()}")
                continue
            name = m.group(1)
            var = self.vm.map(name, is_array=True)
            size = self.ec.convert(m.group(2))
            fill = '""' if name.rstrip().endswith("$") else "0"
            try:
                count = str(int(float(size)) + 1)
            except ValueError:
                count = f"({size}) + 1"
            self._emit(f"{var} = [{fill}] * {count}")

    def _emit_on_branch(self, m, cfg, i):
        """ON x GOTO/GOSUB n1, n2, ... -> an if/elif ladder."""
        selector = self.ec.convert(m.group(1))
        is_gosub = m.group(2).upper() == "GOSUB"
        targets = [t.strip() for t in m.group(3).split(",") if t.strip()]
        for index, target in enumerate(targets, start=1):
            keyword = "if" if index == 1 else "elif"
            self._emit(f"{keyword} {selector} == {index}:")
            mark = self._open_block()
            if is_gosub:
                num = int(target)
                name = cfg.func_name_at(num) or f"sub_{num}"
                self._emit(f"{name}()")
            else:
                self._emit_todo(f"GOTO {target} — unresolved computed jump")
            self._close_block(mark)
        return i + 1

    def _emit_imports(self, lines):
        """Scan for needed imports and emit them."""
        # Skip REM lines — prose mentioning "END" is not a statement.
        code = [ln.text for ln in lines if not re.match(r"^REM\b", ln.text)]
        src = " ".join(code)
        body = "\n".join(self.output_lines)

        if re.search(r"\b(SQR|SIN|COS|TAN|ATN|LOG|EXP)\s*\(", src, re.IGNORECASE):
            self._emit("import math")
        if re.search(r"\b(RND|randint|random)\b", src + body, re.IGNORECASE):
            self._emit("import random")
        if re.search(r"\bSLEEP\b", src, re.IGNORECASE):
            self._emit("import time")
        if self._needs_sys or any(re.match(r"^(END|STOP)\s*$", stmt) for stmt in code):
            self._emit("import sys")

    def _emit_runtime(self, body: str):
        """Emit the small shims the converted code depends on."""
        used = [name for name in self.ec.SHIMS if re.search(rf"\b{name}\(", body)]
        if used or self._needs_data:
            self._emit()
            self._emit("# ── BASIC runtime shims " + "─" * 45)
        for name in used:
            self._emit()
            for shim_line in self.ec.SHIMS[name].splitlines():
                self._emit(shim_line)
        if self._needs_data:
            self._emit()
            items = ", ".join(self._data_items) if self._data_items else ""
            self._emit(f"_DATA = [{items}]")
            self._emit("_data_pos = 0")
            self._emit("")
            self._emit("")
            self._emit("def _read():")
            self._emit("    global _data_pos")
            self._emit("    _data_pos += 1")
            self._emit("    return _DATA[_data_pos - 1]")
            self._emit("")
            self._emit("")
            self._emit("def _restore():")
            self._emit("    global _data_pos")
            self._emit("    _data_pos = 0")

    def _split_subroutines(self, lines, cfg):
        """
        Split lines into main body and subroutine bodies.
        Subroutines start at GOSUB targets marked with REM -- name,
        or just at GOSUB targets, and end at RETURN.
        Main body is everything before the first subroutine,
        plus everything after END.
        """
        if not cfg.gosub_targets:
            return lines, []

        # Find the END line that precedes subroutines
        end_idx = None
        for i, line in enumerate(lines):
            if re.match(r"^END\s*$", line.text) and i < len(lines) - 1:
                # Check if any subroutine targets come after this
                following_nums = {ln.number for ln in lines[i + 1 :]}
                if following_nums & cfg.gosub_targets:
                    end_idx = i
                    break

        if end_idx is None:
            return lines, []

        main_lines = lines[:end_idx]  # exclude END itself

        # Group subroutine lines
        sub_lines = lines[end_idx + 1 :]
        subroutines = []
        current_sub_start = None
        current_sub_body = []

        for line in sub_lines:
            if line.number in cfg.gosub_targets or cfg.func_name_at(line.number):
                if current_sub_start is not None:
                    subroutines.append((current_sub_start, current_sub_body))
                current_sub_start = line.number
                current_sub_body = [line]
            else:
                if current_sub_start is not None:
                    current_sub_body.append(line)

        if current_sub_start is not None:
            subroutines.append((current_sub_start, current_sub_body))

        return main_lines, subroutines

    def _split_print_args(self, args: str) -> list:
        """
        Split PRINT args on ; or , at the top level.

        Depth matters: splitting naively tears `MOD(A, 3)` in half at the
        comma and leaves an unconvertible fragment behind.
        """
        parts = []
        current = []
        in_string = False
        depth = 0

        for ch in args:
            if ch == '"':
                in_string = not in_string
                current.append(ch)
            elif in_string:
                current.append(ch)
            elif ch == "(":
                depth += 1
                current.append(ch)
            elif ch == ")":
                depth = max(0, depth - 1)
                current.append(ch)
            elif ch in (";", ",") and depth == 0:
                parts.append("".join(current))
                current = []
            else:
                current.append(ch)

        if current:
            parts.append("".join(current))

        return [p for p in parts if p.strip()]

    def _emit_print(self, args: str):
        """Convert a BASIC PRINT statement to Python print()."""
        if not args:
            self._emit("print()")
            return

        # Split on ; separators (BASIC print separator)
        # But be careful not to split inside strings
        parts = self._split_print_args(args)
        py_parts = [self.ec.convert(p.strip()) for p in parts if p.strip()]

        # Check for trailing semicolon (no newline)
        if args.rstrip().endswith(";"):
            self._emit(f"print({', '.join(py_parts)}, end='')")
        else:
            self._emit(f"print({', '.join(py_parts)})")

    @staticmethod
    def _wrap_input(basic_var: str, call: str) -> str:
        """INPUT into a numeric variable reads a number, not a string."""
        return call if basic_var.rstrip().endswith("$") else f"float({call})"

    def _emit_input(self, var_text: str):
        """Convert INPUT statement."""
        # May have a prompt: INPUT "text"; VAR
        m = re.match(r'^"([^"]*)"\s*[;,]\s*([A-Z][0-9]?\$?)$', var_text, re.IGNORECASE)
        if m:
            prompt = m.group(1)
            basic_var = m.group(2)
            var = self.vm.map(basic_var)
            call = self._wrap_input(basic_var, f'input("{prompt}")')
            self._emit(f"{var} = {call}")
        else:
            # Plain INPUT VAR — a preceding PRINT usually carried the prompt.
            basic_var = var_text.strip()
            var = self.vm.map(basic_var)
            self._emit(f"{var} = {self._wrap_input(basic_var, 'input()')}")

    def _emit_block(self, lines: list, cfg, i: int = 0) -> int:
        """
        Emit a block of BASIC lines as Python.
        Returns the index after the last consumed line.
        Uses recursive descent for structured constructs.
        """
        while i < len(lines):
            line = lines[i]
            text = line.text

            # Skip REM -- name (function header comments)
            if re.match(r"^REM\s+--", text):
                i += 1
                continue

            # REM comment — may be top of a BASIC 2.0 while loop
            # Pattern: REM / IF NOT (cond) THEN GOTO end / body / GOTO here
            if re.match(r"^REM\s*$", text):
                while_result = self._try_emit_b20_while(lines, i, cfg)
                if while_result is not None:
                    i = while_result
                    continue
                if self._indent == 0:
                    self._emit()
                i += 1
                continue

            m = re.match(r"^REM\s+(.*)", text)
            if m:
                # Take the body from the untouched original — a comment is
                # prose, and uppercasing it loses information.
                original = re.match(r"^\s*REM\s+(.*)", line.original, re.IGNORECASE)
                body = original.group(1) if original else m.group(1)
                self._emit_comment(body.rstrip())
                i += 1
                continue

            # EXIT — leaves the enclosing DO loop
            if re.match(r"^EXIT\s*$", text):
                self._emit("break")
                i += 1
                continue

            # RETURN
            if re.match(r"^RETURN\s*$", text):
                # In a subroutine context just stop — def handles the return
                i += 1
                continue

            # END
            if re.match(r"^END\s*$", text):
                self._emit("sys.exit()")
                i += 1
                continue

            # SLEEP
            m = re.match(r"^SLEEP\s+(.+)$", text)
            if m:
                self._emit(f"time.sleep({self.ec.convert(m.group(1))})")
                i += 1
                continue

            # STOP
            if re.match(r"^STOP\s*$", text):
                self._emit("sys.exit()  # STOP")
                self._needs_sys = True
                i += 1
                continue

            # POKE addr, value
            m = re.match(r"^POKE\s+([^,]+),\s*(.+)$", text, re.IGNORECASE)
            if m:
                addr = self.ec.convert(m.group(1))
                val = self.ec.convert(m.group(2))
                self._emit_todo(f"POKE {addr}, {val} — no memory to write")
                i += 1
                continue

            # SYS addr
            m = re.match(r"^SYS\s+(.+)$", text, re.IGNORECASE)
            if m:
                self._emit_todo(
                    f"SYS {self.ec.convert(m.group(1))} — machine code call"
                )
                i += 1
                continue

            # WAIT addr, mask [, xor]
            m = re.match(r"^WAIT\s+(.+)$", text, re.IGNORECASE)
            if m:
                self._emit_todo(f"WAIT {m.group(1)} — hardware poll")
                i += 1
                continue

            # DIM a(n) [, b(m) ...]
            m = re.match(r"^DIM\s+(.+)$", text, re.IGNORECASE)
            if m:
                self._emit_dim(m.group(1))
                i += 1
                continue

            # DATA a, b, c
            m = re.match(r"^DATA\s*(.*)$", text, re.IGNORECASE)
            if m:
                self._data_items.extend(self._split_data(m.group(1)))
                i += 1
                continue

            # READ var [, var ...]
            m = re.match(r"^READ\s+(.+)$", text, re.IGNORECASE)
            if m:
                for target in split_outside_strings(m.group(1), ","):
                    self._emit(f"{self._assign_target(target)} = _read()")
                self._needs_data = True
                i += 1
                continue

            # RESTORE
            if re.match(r"^RESTORE\s*$", text):
                self._emit("_restore()")
                self._needs_data = True
                i += 1
                continue

            # GET var  /  GETKEY var
            m = re.match(r"^GET(KEY)?\s+([A-Z][0-9]?\$?)$", text, re.IGNORECASE)
            if m:
                var = self.vm.map(m.group(2))
                if m.group(1):
                    self._emit(f"{var} = input()[:1]  # GETKEY (waits)")
                else:
                    self._emit(f"{var} = ''  # TODO: GET — non-blocking key read")
                i += 1
                continue

            # DEF FNx(p) = expr
            m = re.match(
                r"^DEF\s+FN\s*([A-Z][0-9]?)\s*\(\s*([A-Z][0-9]?)\s*\)\s*=\s*(.+)$",
                text,
                re.IGNORECASE,
            )
            if m:
                name = f"fn{m.group(1).lower()}"
                param = self.vm.map(m.group(2))
                body = self.ec.convert(m.group(3))
                self._emit(f"def {name}({param}):")
                self._indent += 1
                self._emit(f"return {body}")
                self._indent -= 1
                i += 1
                continue

            # ON x GOTO/GOSUB n1, n2, ...
            m = re.match(r"^ON\s+(.+?)\s+(GOTO|GOSUB)\s+(.+)$", text, re.IGNORECASE)
            if m:
                i = self._emit_on_branch(m, cfg, i)
                continue

            # FOR loop
            m = re.match(
                r"^FOR\s+([A-Z][0-9]?)\s*=\s*(.+?)\s+TO\s+(.+?)(?:\s+STEP\s+(.+))?$",
                text,
                re.IGNORECASE,
            )
            if m:
                i = self._emit_for(lines, i, m, cfg)
                continue

            # GOSUB
            m = re.match(r"^GOSUB\s+(\d+)$", text)
            if m:
                target = int(m.group(1))
                func_name = cfg.func_name_at(target) or f"sub_{target}"
                self._emit(f"{func_name}()")
                i += 1
                continue

            # DO [WHILE|UNTIL cond] ... LOOP [WHILE|UNTIL cond] (BASIC 65/7.0)
            m = re.match(r"^DO(?:\s+(WHILE|UNTIL)\s+(.+))?$", text, re.IGNORECASE)
            if m:
                head = (m.group(1), m.group(2)) if m.group(1) else None
                i = self._emit_do_loop(lines, i, cfg, head)
                continue

            # BEGIN/BEND structured IF (BASIC 65/7.0)
            m = re.match(r"^IF\s+(.+?)\s+THEN\s+BEGIN\s*$", text, re.IGNORECASE)
            if m:
                i = self._emit_begin_bend_if(lines, i, m.group(1), cfg)
                continue

            # IF NOT (cond) THEN GOTO n  — BASIC 2.0 structured pattern
            m = re.match(
                r"^IF\s+NOT\s*\((.+)\)\s+THEN\s+GOTO\s+(\d+)$", text, re.IGNORECASE
            )
            if m:
                i = self._emit_b20_if(lines, i, m, cfg)
                continue

            # IF (cond) THEN GOTO n  — assert/guard pattern
            m = re.match(r"^IF\s+\((.+)\)\s+THEN\s+GOTO\s+(\d+)$", text, re.IGNORECASE)
            if m:
                cond = self.ec.convert_condition(m.group(1))
                target = int(m.group(2))
                # Simple guard — emit as pass-through
                self._emit(f"if {cond}:")
                self._indent += 1
                self._emit("pass  # GOTO target resolved inline")
                self._indent -= 1
                i += 1
                continue

            # IF cond THEN statement  (single line)
            m = re.match(r"^IF\s+(.+?)\s+THEN\s+(.+)$", text, re.IGNORECASE)
            if m and not re.match(
                r"^(BEGIN|GOTO)\b", m.group(2).strip(), re.IGNORECASE
            ):
                cond = self.ec.convert_condition(m.group(1))
                stmt = m.group(2).strip()

                # BASIC 7.0/65 allow `IF c THEN a : ELSE b` on one line.
                then_part, else_part = self._split_then_else(stmt)

                self._emit(f"if {cond}:")
                mark = self._open_block()
                self._emit_inline(then_part, line.number, cfg)
                self._close_block(mark)
                if else_part is not None:
                    self._emit("else:")
                    mark = self._open_block()
                    self._emit_inline(else_part, line.number, cfg)
                    self._close_block(mark)
                i += 1
                continue

            # GOTO n — a jump to one of the enclosing loop's landmarks is a
            # break or a continue; anything else stays a TODO.
            m = re.match(r"^GOTO\s+(\d+)$", text)
            if m:
                target = int(m.group(1))
                keyword = self._loop_keyword(target)
                if keyword:
                    self._emit(keyword)
                else:
                    self._emit_todo(f"GOTO {target} — unresolved jump")
                i += 1
                continue

            # PRINT
            m = re.match(r"^PRINT\s*(.*)", text, re.IGNORECASE)
            if m:
                self._emit_print(m.group(1).strip())
                i += 1
                continue

            # INPUT
            m = re.match(r"^INPUT\s+(.+)$", text, re.IGNORECASE)
            if m:
                self._emit_input(m.group(1).strip())
                i += 1
                continue

            # Array element assignment: A(3) = 5
            m = re.match(
                r"^(?:LET\s+)?([A-Z][0-9]?\$?\s*\(.+\))\s*=\s*([^=].*)$",
                text,
                re.IGNORECASE,
            )
            if m:
                self._emit(
                    f"{self._assign_target(m.group(1))} "
                    f"= {self.ec.convert(m.group(2))}"
                )
                i += 1
                continue

            # Assignment: VAR = expr  or  LET VAR = expr
            m = re.match(
                r"^(?:LET\s+)?([A-Z][0-9]?\$?)\s*=\s*(.+)$", text, re.IGNORECASE
            )
            if m:
                var = self.vm.map(m.group(1))
                val = self.ec.convert(m.group(2))
                self._emit(f"{var} = {val}")
                i += 1
                continue

            # Anything else — emit as comment with TODO
            self._emit_todo(f"Unrecognised: {line.original}")
            i += 1

        return i

    def transpile(self, source: str) -> str:
        parser = BasicParser()
        lines = parser.parse(source)
        if not lines:
            return "# Empty program\n"

        cfg = CFGAnalyser(lines)

        # Arrays have to be known before any expression is converted.
        for line in lines:
            m = re.match(r"^DIM\s+(.+)$", line.text, re.IGNORECASE)
            if not m:
                continue
            for decl in split_outside_strings(m.group(1), ","):
                dm = re.match(r"^([A-Z][0-9]?\$?)\s*\(", decl.strip(), re.IGNORECASE)
                if dm:
                    self.ec.arrays.add(dm.group(1).upper())

        # Split into main body and subroutines
        main_lines, subroutines = self._split_subroutines(lines, cfg)

        # Emit header
        self._emit("#!/usr/bin/env python3")
        self._emit('"""')
        self._emit("Transpiled from Commodore BASIC by basic2py.")
        self._emit("Review TODO comments for unresolved jumps.")
        self._emit('"""')
        self._emit()

        # Emit subroutine definitions first (collect, emit after imports)
        sub_lines_collected = []
        saved_output = self.output_lines
        for sub_start, sub_body in subroutines:
            self.output_lines = []
            self._indent = 0
            func_name = cfg.func_name_at(sub_start) or f"sub_{sub_start}"
            self._emit(f"def {func_name}():")
            mark = self._open_block()
            self._emit_block(sub_body, cfg)
            self._close_block(mark)
            self._emit()
            sub_lines_collected.extend(self.output_lines)

        self.output_lines = saved_output

        # Emit the main body into a buffer too, so the preamble can be
        # built once everything it depends on is known.
        saved_output = self.output_lines
        self.output_lines = []
        self._emit_block(main_lines, cfg)
        main_collected = self.output_lines
        self.output_lines = saved_output

        body = "\n".join(sub_lines_collected + main_collected)

        # Now we know what imports and shims we need — emit them
        self._emit_imports(lines)
        self._emit_runtime(body)
        self._emit()

        if self._unknown_calls:
            self._emit(
                "# TODO: unconverted BASIC calls: "
                + ", ".join(sorted(self._unknown_calls))
            )
            self._emit()

        self.output_lines.extend(sub_lines_collected)
        self.output_lines.extend(main_collected)

        return "\n".join(self.output_lines) + "\n"

    @staticmethod
    def check(result: str) -> str | None:
        """Return a description of any syntax error in the generated code."""
        try:
            ast.parse(result)
        except SyntaxError as e:
            return f"line {e.lineno}: {e.msg}"
        return None

    def _try_emit_b20_while(self, lines, i, cfg):
        """
        Try to detect and emit a BASIC 2.0 while loop pattern:
            [top] REM
            [top+1] IF NOT (cond) THEN GOTO end
            ...body...
            GOTO top
            [end]
        Returns next index if pattern matched, None otherwise.
        """
        if i + 1 >= len(lines):
            return None

        top_line = lines[i]
        next_line = lines[i + 1]

        # Next line must be IF NOT (cond) THEN GOTO n
        m = re.match(
            r"^IF\s+NOT\s*\((.+)\)\s+THEN\s+GOTO\s+(\d+)$",
            next_line.text,
            re.IGNORECASE,
        )
        if not m:
            return None

        cond_text = m.group(1)
        end_target = int(m.group(2))
        top_num = top_line.number

        # Collect every line up to the loop's exit target. The back edge is
        # the LAST `GOTO top`; earlier ones are continues, so stopping at the
        # first would truncate the body.
        body = []
        j = i + 2
        while j < len(lines) and lines[j].number < end_target:
            body.append(lines[j])
            j += 1

        if not body:
            return None
        back_edge = re.match(r"^GOTO\s+(\d+)$", body[-1].text)
        if not back_edge or int(back_edge.group(1)) != top_num:
            return None
        body.pop()

        cond = self.ec.convert_condition(cond_text)
        # Fix = -> == in condition
        cond = re.sub(r"(?<![!<>])=(?!=)", "==", cond)
        cond = re.sub(r"===", "==", cond)

        self._emit(f"while {cond}:")
        mark = self._open_block()
        # Jumping to the top re-tests the condition (continue); jumping to
        # the exit target leaves the loop (break).
        self._loop_stack.append({"continue": top_num, "break": end_target})
        if body:
            self._emit_block(body, cfg)
        self._loop_stack.pop()
        self._close_block(mark)
        return j

    def _emit_for(self, lines, i, m, cfg):
        """Emit a FOR/NEXT loop as a Python for loop."""
        var_basic = m.group(1)
        var_py = self.vm.map(var_basic)
        start = self.ec.convert(m.group(2))
        stop = self.ec.convert(m.group(3))
        step = self.ec.convert(m.group(4)) if m.group(4) else None

        # Collect body until NEXT (the variable name is optional in BASIC)
        i += 1
        body = []
        next_number = None
        while i < len(lines):
            if re.match(rf"^NEXT(\s+{var_basic})?\s*$", lines[i].text, re.IGNORECASE):
                next_number = lines[i].number
                i += 1
                break
            body.append(lines[i])
            i += 1
        after_number = lines[i].number if i < len(lines) else None

        # BASIC's TO bound is inclusive, Python's range() stop is exclusive.
        # Which way to nudge it depends on the direction of travel: a
        # descending loop needs stop-1, not stop+1.
        descending = False
        if step:
            try:
                descending = float(step) < 0
            except ValueError:
                # Non-literal step — assume ascending, but say so.
                self._emit_todo(
                    f"FOR step {step!r} is not a literal; "
                    f"range() bound assumes it is positive"
                )
        delta = -1 if descending else 1

        try:
            stop_expr = str(int(float(stop)) + delta)
        except ValueError:
            stop_expr = f"({stop}) {'-' if delta < 0 else '+'} 1"

        if step:
            range_expr = f"range({start}, {stop_expr}, {step})"
        else:
            range_expr = f"range({start}, {stop_expr})"

        self._emit(f"for {var_py} in {range_expr}:")
        mark = self._open_block()
        # A GOTO to the NEXT is a continue; one to the line past it, a break.
        self._loop_stack.append({"continue": next_number, "break": after_number})
        if body:
            self._emit_block(body, cfg)
        self._loop_stack.pop()
        self._close_block(mark)
        return i

    def _loop_condition(self, keyword: str, expr: str) -> str:
        cond = self.ec.convert_condition(expr)
        return f"not ({cond})" if keyword.upper() == "UNTIL" else cond

    def _emit_do_loop(self, lines, i, cfg, head=None):
        """
        Emit a DO ... LOOP as a Python while loop.

        `DO WHILE cond ... LOOP` tests at the top, like Python. The
        `DO ... LOOP WHILE cond` form tests at the bottom and therefore
        always runs once; that gets a do-while emulation rather than a
        plain while, which would silently change the trip count.
        """
        i += 1
        body = []
        depth = 1
        tail = None
        loop_number = None

        while i < len(lines):
            text = lines[i].text
            if re.match(r"^DO\b", text, re.IGNORECASE):
                depth += 1
                body.append(lines[i])
                i += 1
                continue
            m = re.match(r"^LOOP(?:\s+(WHILE|UNTIL)\s+(.+))?$", text, re.IGNORECASE)
            if m:
                depth -= 1
                if depth == 0:
                    if m.group(1):
                        tail = (m.group(1), m.group(2))
                    loop_number = lines[i].number
                    i += 1
                    break
                body.append(lines[i])
                i += 1
                continue
            body.append(lines[i])
            i += 1
        after_number = lines[i].number if i < len(lines) else None
        frame = {"continue": loop_number, "break": after_number}

        if head is not None:
            self._emit(f"while {self._loop_condition(*head)}:")
            mark = self._open_block()
            self._loop_stack.append(frame)
            if body:
                self._emit_block(body, cfg)
            self._loop_stack.pop()
            self._close_block(mark)
        elif tail is not None:
            # Bottom-tested: run the body, then repeat while the condition holds.
            self._emit("while True:")
            mark = self._open_block()
            self._loop_stack.append(frame)
            if body:
                self._emit_block(body, cfg)
            self._loop_stack.pop()
            self._emit(f"if not ({self._loop_condition(*tail)}):")
            inner = self._open_block()
            self._emit("break")
            self._close_block(inner)
            self._close_block(mark)
        else:
            self._emit("while True:")
            mark = self._open_block()
            self._loop_stack.append(frame)
            if body:
                self._emit_block(body, cfg)
            self._loop_stack.pop()
            self._close_block(mark)
        return i

    def _emit_begin_bend_if(self, lines, i, cond_text, cfg):
        """Emit a BEGIN/BEND structured if block (BASIC 65/7.0)."""
        cond = self.ec.convert_condition(cond_text)
        self._emit(f"if {cond}:")
        mark = self._open_block()
        i += 1

        body = []
        depth = 1

        while i < len(lines):
            text = lines[i].text
            if re.match(r"^IF\s+.+\s+THEN\s+BEGIN", text, re.IGNORECASE):
                depth += 1
                body.append(lines[i])
                i += 1
            elif (
                re.match(r"^BEND\s+ELSE\s+BEGIN\s*$", text, re.IGNORECASE)
                and depth == 1
            ):
                # Emit the if body, then start else
                if body:
                    self._emit_block(body, cfg)
                self._close_block(mark)
                self._emit("else:")
                mark = self._open_block()
                body = []
                i += 1
            elif re.match(r"^BEND\s*$", text, re.IGNORECASE):
                depth -= 1
                if depth == 0:
                    if body:
                        self._emit_block(body, cfg)
                    self._close_block(mark)
                    i += 1
                    break
                else:
                    body.append(lines[i])
                    i += 1
            else:
                body.append(lines[i])
                i += 1

        return i

    def _emit_b20_if(self, lines, i, m, cfg):
        """
        Recover BASIC 2.0 IF NOT (cond) THEN GOTO n patterns.

        Pattern 1: Simple if (no else)
            IF NOT (cond) THEN GOTO end
            ...body...
            [end line]

        Pattern 2: if/else
            IF NOT (cond) THEN GOTO else_start
            ...body...
            GOTO end
            [else_start] ...else body...
            [end]
        """
        cond = self.ec.convert_condition(m.group(1))
        skip_target = int(m.group(2))

        # Collect body lines until we hit skip_target or a GOTO
        i += 1
        body = []
        else_body = []
        found_skip_goto = False
        skip_goto_target = None

        while i < len(lines):
            lnum = lines[i].number
            text = lines[i].text

            if lnum >= skip_target:
                break

            # A GOTO at the end of the body is the skip-else jump — unless
            # it targets an enclosing loop, in which case it is a break or a
            # continue and belongs in the body.
            goto_m = re.match(r"^GOTO\s+(\d+)$", text)
            if goto_m and not self._loop_keyword(int(goto_m.group(1))):
                skip_goto_target = int(goto_m.group(1))
                found_skip_goto = True
                i += 1
                break

            body.append(lines[i])
            i += 1

        self._emit(f"if {cond}:")
        mark = self._open_block()
        if body:
            self._emit_block(body, cfg)
        self._close_block(mark)

        if found_skip_goto and skip_goto_target:
            # Collect else body: from skip_target to skip_goto_target
            while i < len(lines) and lines[i].number < skip_goto_target:
                else_body.append(lines[i])
                i += 1

            if else_body:
                self._emit("else:")
                mark = self._open_block()
                self._emit_block(else_body, cfg)
                self._close_block(mark)

        return i


# ── CLI ────────────────────────────────────────────────────────────────────────


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="basic2py — Transpile Commodore BASIC to Python (best effort)",
        epilog=textwrap.dedent("""
        Handles: FOR/NEXT, GOSUB/RETURN, IF/GOTO patterns, BEGIN/BEND,
        DO/LOOP WHILE, PRINT, INPUT, assignments, and common functions.

        Unresolvable GOTOs are preserved as # TODO comments.
        Variable names are expanded: A->a, A$->a_str, A0->a0

        Examples:
          python3 basic2py.py hello.bas
          python3 basic2py.py hello.bas -o hello.py
        """),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input", help="BASIC source file")
    parser.add_argument("-o", "--output", help="Output Python file (default: stdout)")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if the generated Python does not parse",
    )
    args = parser.parse_args()

    try:
        with open(args.input) as f:
            source = f.read()
    except FileNotFoundError:
        print(f"Error: file not found: {args.input}", file=sys.stderr)
        sys.exit(1)
    except OSError as e:
        print(f"Error: cannot read {args.input}: {e}", file=sys.stderr)
        sys.exit(1)

    t = Transpiler()
    result = t.transpile(source)

    problem = Transpiler.check(result)
    if problem:
        print(
            f"Warning: the generated Python does not parse ({problem}). "
            f"This is a basic2py bug or an unsupported construct — the output "
            f"is still written so you can inspect it.",
            file=sys.stderr,
        )

    if args.output:
        try:
            with open(args.output, "w") as f:
                f.write(result)
        except OSError as e:
            print(f"Error: cannot write {args.output}: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"Written to {args.output}", file=sys.stderr)
    else:
        print(result)

    if problem and args.check:
        sys.exit(1)


if __name__ == "__main__":
    main()
