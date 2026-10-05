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

from __future__ import annotations

import ast
import bisect
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
    for i, chunk in enumerate(re.split(r'("(?:[^"\\]|\\.)*")', text)):
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
        self._cache: dict = {}

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

    #: Calls whose arguments are rearranged rather than renamed. Their
    #: arguments can hold nested calls, so a regex cannot split them.
    STRUCTURED_CALLS: ClassVar[tuple] = (
        "LEFT$",
        "RIGHT$",
        "MID$",
        "INSTR",
        "MOD",
        "UPPER$",
        "LOWER$",
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

    #: CHR$(n) calls that fold back into a Python string escape: the
    #: double quote and the newline, which py2basic spells this way.
    CHR_ESCAPES: ClassVar[dict] = {"34": '\\"', "13": "\\n"}
    _CHR_CALL = r"(?:CHR\$|chr)\s*\(\s*(34|13)\s*\)"
    #: A string literal, including any escapes the CHR$ folding produced.
    STRING_RE = re.compile(r'"(?:[^"\\]|\\.)*"')

    def __init__(self, var_mapper: VarMapper):
        self.vm = var_mapper
        #: BASIC names known to be arrays, from DIM. A(1) is a subscript,
        #: not a call, so the variable mapper has to be told the difference.
        self.arrays: set = set()

    # ── String literals ────────────────────────────────────────────────────

    @staticmethod
    def _escape_backslashes(expr: str) -> str:
        """A backslash is an ordinary character in BASIC; double it for Python."""
        out = []
        in_string = False
        for ch in expr:
            if ch == '"':
                in_string = not in_string
            elif ch == "\\" and in_string:
                out.append("\\")
            out.append(ch)
        return "".join(out)

    def _convert_chr(self, expr: str) -> str:
        """
        Fold CHR$(34) and CHR$(13) concatenation back into string escapes.

        The quote has to be *escaped* on the way in. Splicing a bare `"` in
        produces `print("say "hi")`, which is a SyntaxError rather than a
        program.

          "say " + CHR$(34) + "hi" + CHR$(34)  ->  "say \\"hi\\""
          "a" + CHR$(13) + "b"                 ->  "a\\nb"
        """
        esc = self.CHR_ESCAPES
        # Between two literals: "a" + CHR$(34) + "b"  ->  "a\"b"
        expr = re.sub(
            rf'"\s*\+\s*{self._CHR_CALL}\s*\+\s*"',
            lambda m: esc[m.group(1)],
            expr,
            flags=re.IGNORECASE,
        )
        # Trailing: "a" + CHR$(34)  ->  "a\""
        expr = re.sub(
            rf'"\s*\+\s*{self._CHR_CALL}',
            lambda m: esc[m.group(1)] + '"',
            expr,
            flags=re.IGNORECASE,
        )
        # Leading: CHR$(34) + "a"  ->  "\"a"
        expr = re.sub(
            rf'{self._CHR_CALL}\s*\+\s*"',
            lambda m: '"' + esc[m.group(1)],
            expr,
            flags=re.IGNORECASE,
        )
        return expr

    # ── Calls with structure ───────────────────────────────────────────────

    @staticmethod
    def _matching_paren(s: str, open_idx: int) -> int:
        """Index of the ) closing the ( at open_idx, or -1."""
        depth = 0
        in_string = False
        for k in range(open_idx, len(s)):
            ch = s[k]
            if ch == '"':
                in_string = not in_string
            elif in_string:
                continue
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return k
        return -1

    @staticmethod
    def _split_top_level(s: str) -> list:
        """Split an argument list on the commas outside nested parentheses."""
        args = []
        current: list = []
        depth = 0
        in_string = False
        for ch in s:
            if ch == '"':
                in_string = not in_string
            elif not in_string:
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                elif ch == "," and depth == 0:
                    args.append("".join(current))
                    current = []
                    continue
            current.append(ch)
        args.append("".join(current))
        return [a.strip() for a in args if a.strip()]

    def _call_pattern(self):
        names = [re.escape(n) for n in self.STRUCTURED_CALLS]
        names += [re.escape(n) for n in sorted(self.arrays, key=len, reverse=True)]
        names.append(r"FN[A-Z][0-9]?")
        # The lookbehind stops MID$( from being read as the array D$(.
        return re.compile(r"(?<![A-Z0-9$_.])(" + "|".join(names) + r")\s*\(")

    def _render_call(self, name: str, args: list):
        """Python for one structured call, or None if the arity is wrong."""
        n = len(args)
        if name in self.arrays:
            # A(3) -> a_arr[3]
            return f"{self.vm.map(name, is_array=True)}[{', '.join(args)}]"
        if re.fullmatch(r"FN[A-Z][0-9]?", name):
            return f"{name.lower()}({', '.join(args)})"
        if name == "LEFT$" and n == 2:
            return f"({args[0]})[:{args[1]}]"
        if name == "RIGHT$" and n == 2:
            return f"({args[0]})[-({args[1]}):]"
        if name == "MID$" and n == 3:
            s, start, length = args
            return f"({s})[({start}) - 1:({start}) - 1 + ({length})]"
        if name == "MID$" and n == 2:
            return f"({args[0]})[({args[1]}) - 1:]"
        if name == "INSTR" and n == 2:
            # INSTR is 1-based and 0 when absent; .find() is 0-based and
            # -1, so the offset lines up exactly.
            return f"({args[0]}).find({args[1]}) + 1"
        if name == "INSTR" and n == 3:
            return f"({args[0]}).find({args[1]}, ({args[2]}) - 1) + 1"
        if name == "MOD" and n == 2:
            return f"({args[0]}) % ({args[1]})"
        if name == "UPPER$" and n == 1:
            return f"({args[0]}).upper()"
        if name == "LOWER$" and n == 1:
            return f"({args[0]}).lower()"
        return None

    def _rewrite_calls(self, expr: str) -> str:
        """
        Convert every structured call, innermost first.

        Each call's arguments are found by matching parentheses, not by a
        regex, so LEFT$(S$, LEN(S$) - 1) splits where BASIC splits it.
        """
        pattern = self._call_pattern()
        pos = 0
        while True:
            m = pattern.search(expr, pos)
            if m is None:
                return expr
            open_idx = m.end() - 1
            close_idx = self._matching_paren(expr, open_idx)
            if close_idx < 0:
                return expr  # unbalanced — leave the rest for the TODO scan
            raw_args = self._split_top_level(expr[open_idx + 1 : close_idx])
            args = [self._convert_code(a) for a in raw_args]
            replacement = self._render_call(m.group(1), args)
            if replacement is None:
                pos = m.end()
                continue
            expr = expr[: m.start()] + replacement + expr[close_idx + 1 :]
            pos = m.start() + len(replacement)

    # ── Everything else ────────────────────────────────────────────────────

    def _map_vars_in_expr(self, expr: str) -> str:
        """Map BASIC variable names to Python names, avoiding function names."""
        # Map string vars first (A$ before A)
        expr = re.sub(r"\b([A-Z][0-9]?)\$", lambda m: self.vm.map(m.group(0)), expr)
        # Map numeric vars — but not if followed by ( (function call)
        return re.sub(
            r"\b([A-Z][0-9]?)\b(?!\s*\()", lambda m: self.vm.map(m.group(0)), expr
        )

    def _convert_code(self, expr: str) -> str:
        """
        Convert BASIC code whose string literals have been stashed.

        The input is uppercase outside strings and the Python it produces
        is lowercase, so no rewrite can fire twice on its own output.
        """
        expr = self._rewrite_calls(expr)

        # Simple renames.
        for basic_fn, py_fn in self.SIMPLE_FUNCTIONS:
            expr = re.sub(rf"\b{basic_fn}\s*\(", f"{py_fn}(", expr)

        # Operators
        expr = expr.replace("^", "**")
        expr = re.sub(r"\bAND\b", "and", expr)
        expr = re.sub(r"\bOR\b", "or", expr)
        expr = re.sub(r"\bNOT\b", "not", expr)
        expr = expr.replace("<>", "!=")

        expr = self._map_vars_in_expr(expr)

        # Clean up double negation from NOT NOT patterns
        return re.sub(r"\bnot\s+not\b", "", expr)

    def convert(self, expr: str) -> str:
        expr = self._escape_backslashes(expr.strip())
        expr = self._convert_chr(expr)

        # Nothing below may touch the inside of a string literal, so the
        # literals sit out the conversion as placeholders.
        strings = []

        def stash(m):
            strings.append(m.group(0))
            return f"__STR{len(strings) - 1}__"

        expr = self.STRING_RE.sub(stash, expr)
        expr = self._convert_code(expr)
        for i, s in enumerate(strings):
            expr = expr.replace(f"__STR{i}__", s)
        return expr.strip()

    @staticmethod
    def _balanced(s: str) -> bool:
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

    @staticmethod
    def negate(cond: str) -> str:
        """`not (cond)`, or the inside of a condition that already is one."""
        if cond.startswith("not (") and cond.endswith(")"):
            inner = cond[5:-1]
            if ExprConverter._balanced(inner):
                return inner
        return f"not ({cond})"


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
        # BASIC 7.0 spells the else branch BEND : ELSE BEGIN on one line.
        if re.match(r"^\s*BEND\s*:?\s*ELSE\s+BEGIN\s*$", upper):
            return [text]
        return split_outside_strings(text, ":")

    def parse(self, source: str) -> list:
        lines: list = []
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
        self.goto_targets: set = set()
        self.gosub_targets: set = set()
        self.func_names: dict = {}  # line_number -> name
        #: Every line number in the program, for line_after().
        self.numbers = sorted({ln.number for ln in lines})

        self._scan()

    def is_subroutine_start(self, line_num: int) -> bool:
        return line_num in self.gosub_targets

    def func_name_at(self, line_num: int) -> str | None:
        return self.func_names.get(line_num)

    def line_after(self, line_num: int) -> int | None:
        """The number of the line following line_num in the whole program."""
        index = bisect.bisect_right(self.numbers, line_num)
        return self.numbers[index] if index < len(self.numbers) else None


# ── Main transpiler ────────────────────────────────────────────────────────────


class Transpiler:

    def __init__(self):
        self.vm = VarMapper()
        self.ec = ExprConverter(self.vm)
        self.output_lines: list = []
        self._indent = 0
        self._needs_sys = False
        self._needs_data = False
        self._data_items: list = []
        self._unknown_calls: set = set()
        #: Jump targets that mean break/continue inside the enclosing loop.
        self._loop_stack: list = []
        #: True while a subroutine body is being emitted, so RETURN means return.
        self._in_sub = False

    def _flag_unknown_calls(self, text: str):
        """
        Warn about calls this converter did not recognise.

        Without this a BASIC function with no mapping survives as a bare
        name — `a = SPRCOLOR(1)` parses cleanly and then raises NameError
        at runtime, which is worse than an honest TODO. The parser
        uppercases all code, and everything this converter emits is
        lowercase, so an uppercase letter in a call name means it got
        through untouched.
        """
        code = ExprConverter.STRING_RE.sub('""', text)
        for m in re.finditer(r"\b([A-Za-z_][A-Za-z_0-9.$]*)\s*\(", code):
            name = m.group(1)
            if name != name.lower():
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

    def _emit_imports(self, lines, body: str):
        """Emit the imports the converted program needs."""
        # Skip REM lines — prose mentioning "SLEEP" is not a statement.
        code = [ln.text for ln in lines if not re.match(r"^REM\b", ln.text)]
        src = " ".join(code)

        if re.search(r"\bmath\.", body):
            self._emit("import math")
        if re.search(r"\b_rnd\(", body):
            self._emit("import random")
        if re.search(r"\bSLEEP\b", src):
            self._emit("import time")
        # sys is needed wherever an END or STOP was emitted, including
        # inside a single-line IF, so it is tracked at emission time.
        if self._needs_sys:
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
        # py2basic spells Python's default separator as an explicit " "
        # between two strings; fold it back into print()'s own.
        last = len(py_parts) - 1
        py_parts = [
            p for k, p in enumerate(py_parts) if not (p == '" "' and 0 < k < last)
        ]

        # Check for trailing semicolon (no newline)
        if args.rstrip().endswith(";"):
            self._emit(f"print({', '.join(py_parts)}, end='')")
        else:
            self._emit(f"print({', '.join(py_parts)})")

    @staticmethod
    def _wrap_input(basic_var: str, call: str) -> str:
        """INPUT into a numeric variable reads a number, not a string."""
        is_string = re.match(r"^[A-Z][0-9]?\$", basic_var.strip(), re.IGNORECASE)
        return call if is_string else f"float({call})"

    def _emit_input(self, var_text: str):
        """Convert INPUT [prompt;] var [, var ...]."""
        prompt = None
        m = re.match(r'^"([^"]*)"\s*[;,]\s*(.+)$', var_text)
        if m:
            prompt, var_text = m.group(1), m.group(2)
        # INPUT A, B reads one value per variable; only the first input()
        # carries the prompt, as BASIC shows it once.
        for index, target in enumerate(split_outside_strings(var_text, ",")):
            if prompt is not None and index == 0:
                call = f'input("{prompt}")'
            else:
                call = "input()"
            self._emit(
                f"{self._assign_target(target)} = {self._wrap_input(target, call)}"
            )

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

            # A later GOTO back to this line makes it the top of a loop.
            loop_end = self._try_emit_b20_loop(lines, i, cfg)
            if loop_end is not None:
                i = loop_end
                continue

            # Bare REM — a blank line at top level.
            if re.match(r"^REM\s*$", text):
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
                # The RETURN that ends a subroutine is dropped before its
                # body is emitted, so any that remains is an early return.
                if self._in_sub:
                    self._emit("return")
                else:
                    self._emit_todo("RETURN outside a subroutine")
                i += 1
                continue

            # END
            if re.match(r"^END\s*$", text):
                self._emit("sys.exit()")
                self._needs_sys = True
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
                i = self._emit_b20_if(lines, i, m.group(1), int(m.group(2)), cfg)
                continue

            # IF cond THEN GOTO n / IF cond THEN n — the body is skipped
            # when the condition holds; py2basic's assert looks like this.
            m = re.match(
                r"^IF\s+(.+?)\s+THEN\s+(?:GOTO\s+)?(\d+)$", text, re.IGNORECASE
            )
            if m:
                i = self._emit_b20_if(
                    lines, i, m.group(1), int(m.group(2)), cfg, skip_when_true=True
                )
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
        sub_lines_collected: list = []
        saved_output = self.output_lines
        for sub_start, sub_body in subroutines:
            self.output_lines = []
            self._indent = 0
            func_name = cfg.func_name_at(sub_start) or f"sub_{sub_start}"
            self._emit(f"def {func_name}():")
            mark = self._open_block()
            # The closing RETURN is the def's own; any other is an early return.
            if sub_body and re.match(r"^RETURN\s*$", sub_body[-1].text):
                sub_body = sub_body[:-1]
            self._in_sub = True
            self._emit_block(sub_body, cfg)
            self._in_sub = False
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
        self._emit_imports(lines, body)
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

    def _try_emit_b20_loop(self, lines, i, cfg):
        """
        Recover a BASIC 2.0 loop whose back edge is a GOTO to line i.

            [top] IF NOT (cond) THEN GOTO end      while cond:
                  ...body...
                  GOTO top
            [end]

            [top] IF (cond) THEN GOTO end          while not cond:
            [top] ...body... GOTO top              while True:

        The older shape with a bare REM above the IF NOT is accepted too.
        Returns the index after the loop, or None if line i starts no loop.
        """
        top = lines[i].number
        back_edge = None
        for k in range(i + 1, len(lines)):
            m = re.match(r"^GOTO\s+(\d+)$", lines[k].text)
            if m and int(m.group(1)) == top:
                # The LAST jump back is the back edge; earlier ones are
                # continues, so stopping at the first would cut the body.
                back_edge = k
        if back_edge is None:
            return None
        after = cfg.line_after(lines[back_edge].number)

        exit_re = r"^IF\s+(NOT\s*)?\((.+)\)\s+THEN\s+GOTO\s+(\d+)$"
        head = re.match(exit_re, lines[i].text, re.IGNORECASE)
        body_start = i + 1
        if not head and re.match(r"^REM\s*$", lines[i].text) and i + 1 < back_edge:
            head = re.match(exit_re, lines[i + 1].text, re.IGNORECASE)
            body_start = i + 2

        if head and int(head.group(3)) == after:
            cond = self.ec.convert_condition(head.group(2))
            if not head.group(1):
                cond = self.ec.negate(cond)
            self._emit(f"while {cond}:")
            body = lines[body_start:back_edge]
        else:
            self._emit("while True:")
            body = lines[i:back_edge]

        mark = self._open_block()
        # Jumping to the top re-tests the condition (continue); jumping to
        # the exit target leaves the loop (break).
        self._loop_stack.append({"continue": top, "break": after})
        if body:
            self._emit_block(body, cfg)
        self._loop_stack.pop()
        self._close_block(mark)
        return back_edge + 1

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
        # The break target is the line after NEXT in the whole program; the
        # slice being emitted may end at the NEXT.
        after_number = cfg.line_after(next_number) if next_number else None

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
        after_number = cfg.line_after(loop_number) if loop_number else None
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
                re.match(r"^BEND\s*:?\s*ELSE\s+BEGIN\s*$", text, re.IGNORECASE)
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

    def _try_emit_assert(self, lines, i, target, cond):
        """
        py2basic's assert:

            IF (cond) THEN GOTO n
            PRINT "message"
            END
            [n]

        Returns the index after the END, or None if that is not the shape.
        """
        if i + 1 >= len(lines) or lines[i + 1].number >= target:
            return None
        if i + 2 < len(lines) and lines[i + 2].number < target:
            return None
        print_m = re.match(r"^PRINT\s+(.+)$", lines[i].text)
        if not print_m or not re.match(r"^END\s*$", lines[i + 1].text):
            return None
        message = self.ec.convert(print_m.group(1))
        if message == '"ASSERTION FAILED"':
            self._emit(f"assert {cond}")
        else:
            self._emit(f"assert {cond}, {message}")
        return i + 2

    def _emit_b20_if(self, lines, i, cond_src, target, cfg, skip_when_true=False):
        """
        Recover an IF that jumps past its body.

            IF NOT (cond) THEN GOTO end       if cond:
            ...body...                            body
            [end]

            IF NOT (cond) THEN GOTO else      if cond:
            ...body...                            body
            GOTO end                          else:
            [else] ...else body...                else body
            [end]

        skip_when_true marks the `IF cond THEN GOTO n` form, whose body runs
        when the condition is false. When that body is a PRINT and an END it
        is the assert py2basic emits, and when n is a landmark of the
        enclosing loop it is a conditional break or continue.
        """
        cond = self.ec.convert_condition(cond_src)
        here = lines[i].number

        if skip_when_true:
            keyword = self._loop_keyword(target)
            if keyword:
                self._emit(f"if {cond}:")
                mark = self._open_block()
                self._emit(keyword)
                self._close_block(mark)
                return i + 1
            if target <= here:
                # A jump backwards is a loop this converter did not recover.
                self._emit(f"if {cond}:")
                mark = self._open_block()
                self._emit_todo(f"GOTO {target} — unresolved jump")
                self._close_block(mark)
                return i + 1
            assert_end = self._try_emit_assert(lines, i + 1, target, cond)
            if assert_end is not None:
                return assert_end
            cond = self.ec.negate(cond)

        i += 1
        body = []
        while i < len(lines) and lines[i].number < target:
            body.append(lines[i])
            i += 1

        # The skip-else jump is the body's LAST line: a GOTO past the else
        # branch that is not a break or continue. Any earlier GOTO belongs
        # to a nested construct.
        else_end = None
        if body:
            gm = re.match(r"^GOTO\s+(\d+)$", body[-1].text)
            if gm:
                jump = int(gm.group(1))
                if jump > target and not self._loop_keyword(jump):
                    else_end = jump
                    body.pop()

        self._emit(f"if {cond}:")
        mark = self._open_block()
        if body:
            self._emit_block(body, cfg)
        self._close_block(mark)

        if else_end is not None:
            else_body = []
            while i < len(lines) and lines[i].number < else_end:
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
