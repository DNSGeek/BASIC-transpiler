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

import re
import sys
import textwrap
from dataclasses import dataclass, field
from typing import Optional

# ── Data structures ────────────────────────────────────────────────────────────


@dataclass
class BasicLine:
    number: int
    text: str  # original statement text (uppercased, stripped)
    original: str  # original as-is


@dataclass
class PyBlock:
    """A recovered Python code block."""

    lines: list = field(default_factory=list)  # strings

    def add(self, line: str):
        self.lines.append(line)

    def output(self, indent: int = 0) -> str:
        prefix = "    " * indent
        return "\n".join(prefix + l for l in self.lines)


# ── Variable name mapper ───────────────────────────────────────────────────────


class VarMapper:
    """
    Maps BASIC variable names to Python names.
    A   -> a
    A$  -> a_str
    A0  -> a0
    A0$ -> a0_str
    """

    def __init__(self):
        self._cache = {}

    def map(self, basic_name: str) -> str:
        if basic_name in self._cache:
            return self._cache[basic_name]

        name = basic_name.strip()
        is_string = name.endswith("$")
        if is_string:
            name = name[:-1]

        # Lowercase and clean
        py_name = name.lower()
        if is_string:
            py_name += "_str"

        self._cache[basic_name] = py_name
        return py_name

    def map_expr(self, expr: str) -> str:
        """Map all variable references within an expression string."""
        # Match BASIC variable names: letter optionally followed by digit, optionally followed by $
        # Must be careful not to match inside function names like STR$, CHR$ etc.
        # We handle those separately in expression conversion.
        result = re.sub(r"\b([A-Z][0-9]?)\$", lambda m: self.map(m.group(0)), expr)
        result = re.sub(
            r"\b([A-Z][0-9]?)\b(?!\$|\()", lambda m: self.map(m.group(0)), result
        )
        return result


# ── Expression converter ───────────────────────────────────────────────────────


class ExprConverter:
    """Converts BASIC expressions to Python expressions."""

    def __init__(self, var_mapper: VarMapper):
        self.vm = var_mapper
        self._needs_math = False
        self._needs_time = False

    def _convert_chr34(self, expr: str) -> str:
        """Convert CHR$(34) concatenation back to quoted strings."""
        # "hello" + CHR$(34) + "world" -> 'hello"world'
        # This is cosmetic — leaves as f-string friendly form
        expr = re.sub(
            r'"\s*\+\s*(?:CHR\$\s*\(\s*34\s*\)|chr\s*\(\s*34\s*\))\s*\+\s*"',
            '"',
            expr,
            flags=re.IGNORECASE,
        )
        # Trailing CHR$(34): "hello" + CHR$(34) -> 'hello"'
        expr = re.sub(
            r'"\s*\+\s*(?:CHR\$\s*\(\s*34\s*\)|chr\s*\(\s*34\s*\))',
            '"',
            expr,
            flags=re.IGNORECASE,
        )
        # Leading CHR$(34): CHR$(34) + "hello" -> '"hello'
        expr = re.sub(
            r'(?:CHR\$\s*\(\s*34\s*\)|chr\s*\(\s*34\s*\))\s*\+\s*"',
            '"',
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

    def convert(self, expr: str) -> str:
        expr = expr.strip()

        # String concatenation: " + CHR$(34) + " patterns
        expr = self._convert_chr34(expr)

        # Function conversions (order matters - most specific first)
        expr = re.sub(r"\bSTR\$\s*\(", "str(", expr, flags=re.IGNORECASE)
        expr = re.sub(r"\bCHR\$\s*\(", "chr(", expr, flags=re.IGNORECASE)
        expr = re.sub(r"\bASC\s*\(", "ord(", expr, flags=re.IGNORECASE)
        expr = re.sub(r"\bLEN\s*\(", "len(", expr, flags=re.IGNORECASE)
        expr = re.sub(r"\bINT\s*\(", "int(", expr, flags=re.IGNORECASE)
        expr = re.sub(r"\bABS\s*\(", "abs(", expr, flags=re.IGNORECASE)
        expr = re.sub(r"\bSQR\s*\(", "math.sqrt(", expr, flags=re.IGNORECASE)
        expr = re.sub(r"\bSIN\s*\(", "math.sin(", expr, flags=re.IGNORECASE)
        expr = re.sub(r"\bCOS\s*\(", "math.cos(", expr, flags=re.IGNORECASE)
        expr = re.sub(r"\bTAN\s*\(", "math.tan(", expr, flags=re.IGNORECASE)
        expr = re.sub(r"\bATN\s*\(", "math.atan(", expr, flags=re.IGNORECASE)
        expr = re.sub(r"\bLOG\s*\(", "math.log(", expr, flags=re.IGNORECASE)
        expr = re.sub(r"\bEXP\s*\(", "math.exp(", expr, flags=re.IGNORECASE)
        expr = re.sub(
            r"\bUPPER\$\s*\(([^)]+)\)", r"(\1).upper()", expr, flags=re.IGNORECASE
        )
        expr = re.sub(
            r"\bLOWER\$\s*\(([^)]+)\)", r"(\1).lower()", expr, flags=re.IGNORECASE
        )

        if "math." in expr:
            self._needs_math = True

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
        # Replace bare = with == (not inside strings, not !=, <=, >=)
        result = re.sub(r"(?<![!<>])=(?!=)", "==", result)
        # Fix any === that might have been created
        result = re.sub(r"===", "==", result)
        return result


# ── BASIC parser ───────────────────────────────────────────────────────────────


class BasicParser:
    """Parse BASIC source into a list of BasicLine objects."""

    def parse(self, source: str) -> list:
        lines = []
        for raw_line in source.strip().splitlines():
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            # Match line number
            m = re.match(r"^(\d+)\s*(.*)", raw_line)
            if not m:
                continue
            num = int(m.group(1))
            text = m.group(2).strip()
            lines.append(BasicLine(number=num, text=text.upper(), original=text))
        lines.sort(key=lambda l: l.number)
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
        self.line_map = {l.number: i for i, l in enumerate(lines)}
        self.goto_targets = set()
        self.gosub_targets = set()
        self.func_names = {}  # line_number -> name

        self._scan()

    def is_subroutine_start(self, line_num: int) -> bool:
        return line_num in self.gosub_targets

    def func_name_at(self, line_num: int) -> Optional[str]:
        return self.func_names.get(line_num)


# ── Main transpiler ────────────────────────────────────────────────────────────


class Transpiler:
    def __init__(self):
        self.vm = VarMapper()
        self.ec = ExprConverter(self.vm)
        self.output_lines = []
        self._indent = 0
        self._needs_math = False
        self._needs_time = False
        self._needs_sys = False

    def _emit(self, text: str = ""):
        prefix = "    " * self._indent
        self.output_lines.append(prefix + text)

    def _emit_comment(self, text: str):
        self._emit(f"# {text}")

    def _emit_todo(self, text: str):
        self._emit(f"# TODO: {text}")

    def _emit_imports(self, lines):
        """Scan for needed imports and emit them."""
        src = " ".join(l.text for l in lines)
        needs_math = bool(
            re.search(r"\b(SQR|SIN|COS|TAN|ATN|LOG|EXP)\b", src, re.IGNORECASE)
        )
        needs_time = bool(re.search(r"\bSLEEP\b", src, re.IGNORECASE))
        needs_sys = bool(re.search(r"\bEND\b", src, re.IGNORECASE))

        if needs_math:
            self._emit("import math")
        if needs_time:
            self._emit("import time")
        if needs_sys:
            self._emit("import sys")

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
                following_nums = {l.number for l in lines[i + 1 :]}
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

    def _last_was_return(self, lines):
        for line in reversed(lines):
            if re.match(r"^RETURN\s*$", line.text):
                return True
            break
        return False

    def _split_print_args(self, args: str) -> list:
        """Split PRINT args on ; or , outside of string literals."""
        parts = []
        current = []
        in_string = False

        for ch in args:
            if ch == '"':
                in_string = not in_string
                current.append(ch)
            elif ch in (";", ",") and not in_string:
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

    def _emit_input(self, var_text: str):
        """Convert INPUT statement."""
        # May have a prompt: INPUT "text"; VAR
        m = re.match(r'^"([^"]+)"\s*[;,]\s*([A-Z][0-9]?\$?)$', var_text, re.IGNORECASE)
        if m:
            prompt = m.group(1)
            var = self.vm.map(m.group(2))
            self._emit(f'{var} = input("{prompt}")')
        else:
            # Plain INPUT VAR — check if there was a preceding PRINT for the prompt
            var = self.vm.map(var_text.strip())
            self._emit(f"{var} = input()")

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
                self._emit_comment(m.group(1))
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

            # DO / LOOP WHILE (BASIC 65/7.0)
            if re.match(r"^DO\s*$", text):
                i = self._emit_do_loop(lines, i, cfg)
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
                self._emit(f"if {cond}:")
                self._indent += 1
                # Recursively emit the single statement
                fake_line = BasicLine(number=line.number, text=stmt, original=stmt)
                self._emit_block([fake_line], cfg, 0)
                self._indent -= 1
                i += 1
                continue

            # GOTO n — try to identify as while-loop back edge, else TODO
            m = re.match(r"^GOTO\s+(\d+)$", text)
            if m:
                target = int(m.group(1))
                # If it jumps backward it might be a while back-edge (already handled)
                # If it jumps forward it's a skip — might be else end
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
            self._indent = 1
            self._emit_block(sub_body, cfg)
            if not sub_body or not self._last_was_return(sub_body):
                pass  # return already emitted or not needed
            self._indent = 0
            self._emit()
            sub_lines_collected.extend(self.output_lines)

        self.output_lines = saved_output

        # Now we know what imports we need — emit them
        self._emit_imports(lines)
        self._emit()

        # Emit subroutine definitions
        for line in sub_lines_collected:
            self.output_lines.append(line)

        # Emit main code
        self._emit_block(main_lines, cfg)

        return "\n".join(self.output_lines) + "\n"

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

        # Collect body: lines after the IF NOT until we find GOTO top_num
        body = []
        j = i + 2
        found_goto_top = False

        while j < len(lines):
            if lines[j].number >= end_target:
                break
            bm = re.match(r"^GOTO\s+(\d+)$", lines[j].text)
            if bm and int(bm.group(1)) == top_num:
                found_goto_top = True
                j += 1
                break
            body.append(lines[j])
            j += 1

        if not found_goto_top:
            return None

        cond = self.ec.convert_condition(cond_text)
        # Fix = -> == in condition
        cond = re.sub(r"(?<![!<>])=(?!=)", "==", cond)
        cond = re.sub(r"===", "==", cond)

        self._emit(f"while {cond}:")
        self._indent += 1
        if body:
            self._emit_block(body, cfg)
        else:
            self._emit("pass")
        self._indent -= 1
        return j

    def _emit_for(self, lines, i, m, cfg):
        """Emit a FOR/NEXT loop as a Python for loop."""
        var_basic = m.group(1)
        var_py = self.vm.map(var_basic)
        start = self.ec.convert(m.group(2))
        stop = self.ec.convert(m.group(3))
        step = self.ec.convert(m.group(4)) if m.group(4) else None

        # Collect body until NEXT var
        i += 1
        body = []
        while i < len(lines):
            next_m = re.match(rf"^NEXT\s+{var_basic}\s*$", lines[i].text, re.IGNORECASE)
            if next_m:
                i += 1
                break
            body.append(lines[i])
            i += 1

        # Build range()
        # stop in BASIC is inclusive; we need stop+1
        # Try to adjust numerically if possible
        try:
            stop_val = int(float(stop))
            stop_expr = str(stop_val + 1)
        except ValueError:
            stop_expr = f"({stop}) + 1"

        if step:
            range_expr = f"range({start}, {stop_expr}, {step})"
        else:
            range_expr = f"range({start}, {stop_expr})"

        self._emit(f"for {var_py} in {range_expr}:")
        self._indent += 1
        before = len(self.output_lines)
        if body:
            self._emit_block(body, cfg)
        # If nothing substantive was emitted, add pass
        substantive = [
            l
            for l in self.output_lines[before:]
            if l.strip() and not l.strip().startswith("#")
        ]
        if not substantive:
            self._emit("pass")
        self._indent -= 1
        return i

    def _emit_do_loop(self, lines, i, cfg):
        """Emit a DO / LOOP WHILE as a Python while loop."""
        i += 1
        body = []
        cond_expr = "True"

        while i < len(lines):
            text = lines[i].text
            m = re.match(r"^LOOP\s+WHILE\s+(.+)$", text, re.IGNORECASE)
            if m:
                cond_expr = self.ec.convert_condition(m.group(1))
                i += 1
                break
            m = re.match(r"^LOOP\s+UNTIL\s+(.+)$", text, re.IGNORECASE)
            if m:
                cond_expr = f"not ({self.ec.convert_condition(m.group(1))})"
                i += 1
                break
            if re.match(r"^LOOP\s*$", text):
                i += 1
                break
            body.append(lines[i])
            i += 1

        self._emit(f"while {cond_expr}:")
        self._indent += 1
        if body:
            self._emit_block(body, cfg)
        else:
            self._emit("pass")
        self._indent -= 1
        return i

    def _emit_begin_bend_if(self, lines, i, cond_text, cfg):
        """Emit a BEGIN/BEND structured if block (BASIC 65/7.0)."""
        cond = self.ec.convert_condition(cond_text)
        self._emit(f"if {cond}:")
        self._indent += 1
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
                else:
                    self._emit("pass")
                self._indent -= 1
                self._emit("else:")
                self._indent += 1
                body = []
                i += 1
            elif re.match(r"^BEND\s*$", text, re.IGNORECASE):
                depth -= 1
                if depth == 0:
                    if body:
                        self._emit_block(body, cfg)
                    else:
                        self._emit("pass")
                    self._indent -= 1
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

            # Check for GOTO at end of body (skip-else jump)
            goto_m = re.match(r"^GOTO\s+(\d+)$", text)
            if goto_m:
                skip_goto_target = int(goto_m.group(1))
                found_skip_goto = True
                i += 1
                break

            body.append(lines[i])
            i += 1

        self._emit(f"if {cond}:")
        self._indent += 1
        if body:
            self._emit_block(body, cfg)
        else:
            self._emit("pass")
        self._indent -= 1

        if found_skip_goto and skip_goto_target:
            # Collect else body: from skip_target to skip_goto_target
            while i < len(lines) and lines[i].number < skip_goto_target:
                else_body.append(lines[i])
                i += 1

            if else_body:
                self._emit("else:")
                self._indent += 1
                self._emit_block(else_body, cfg)
                self._indent -= 1

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
    args = parser.parse_args()

    try:
        with open(args.input) as f:
            source = f.read()
    except FileNotFoundError:
        print(f"Error: file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    t = Transpiler()
    result = t.transpile(source)

    if args.output:
        with open(args.output, "w") as f:
            f.write(result)
        print(f"Written to {args.output}", file=sys.stderr)
    else:
        print(result)


if __name__ == "__main__":
    main()
