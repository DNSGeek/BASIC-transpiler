"""
py2basic - Python to Commodore BASIC Transpiler
================================================
Transpiles a constrained subset of Python into Commodore BASIC source code.

Dialects:
  --basic2   BASIC 2.0  (C64 default) - structured via GOTO spaghetti
  --basic65  BASIC 65   (MEGA65)      - BEGIN/BEND, DO/LOOP WHILE, MOD()
  --basic7   BASIC 7.0  (C128)        - BEGIN/BEND, DO/LOOP, no MOD()

Supported Python subset:
  - Numeric variables (float)
  - String variables (inferred from assignment)
  - print()                     -> PRINT
  - input()                     -> INPUT
  - int(), float(), str()       -> INT(), direct, STR$()
  - len(), abs(), chr(), ord()  -> LEN(), ABS(), CHR$(), ASC()
  - round()                     -> INT(x + 0.5)
  - range() for loops           -> FOR/TO/STEP/NEXT
  - while loops                 -> dialect-specific
  - if/elif/else                -> dialect-specific
  - def (no params, no return)  -> GOSUB/RETURN subroutines
  - Basic math (+,-,*,/,%,**)   -> BASIC ops
  - time.sleep() / sys.exit()   -> SLEEP (B65 only) / END
  - assert                      -> IF NOT / PRINT / END

NOT supported:
  - Function parameters or return values
  - Lists, dicts, sets, tuples
  - Classes, lambda, comprehensions
  - import (except time, sys, math)
  - try/except, with, raise
  - Multiple assignment targets
  - continue
"""

import ast
import re
import sys
import textwrap
from abc import ABC, abstractmethod
from typing import ClassVar

# Commodore BASIC accepts line numbers 0..63999.
MAX_BASIC_LINE = 63999

# ── Errors ────────────────────────────────────────────────────────────────────


class TranspilerError(Exception):
    def __init__(self, msg: str, lineno: int = 0):
        super().__init__(f"Line {lineno}: {msg}" if lineno else msg)
        self.lineno = lineno


# ── Symbol table ──────────────────────────────────────────────────────────────


class SymbolTable:
    LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

    def __init__(self):
        self._numeric: dict = {}
        self._string: dict = {}
        self._num_counter = 0
        self._str_counter = 0

    def _next_numeric_name(self):
        n = self._num_counter
        self._num_counter += 1
        if n < 26:
            return self.LETTERS[n]
        n -= 26
        return self.LETTERS[n // 10] + str(n % 10)

    def _next_string_name(self):
        n = self._str_counter
        self._str_counter += 1
        if n < 26:
            return self.LETTERS[n] + "$"
        n -= 26
        return self.LETTERS[n // 10] + str(n % 10) + "$"

    def get_numeric(self, py_name):
        if py_name not in self._numeric:
            if self._num_counter >= 286:
                raise TranspilerError(
                    f"Too many numeric variables (max 286). Overflow on '{py_name}'"
                )
            self._numeric[py_name] = self._next_numeric_name()
        return self._numeric[py_name]

    def get_string(self, py_name):
        if py_name not in self._string:
            if self._str_counter >= 286:
                raise TranspilerError(
                    f"Too many string variables (max 286). Overflow on '{py_name}'"
                )
            self._string[py_name] = self._next_string_name()
        return self._string[py_name]

    def dump(self):
        lines = ["Variable map:"]
        for py, bas in sorted(self._numeric.items()):
            lines.append(f"  {py:20s} -> {bas}")
        for py, bas in sorted(self._string.items()):
            lines.append(f"  {py:20s} -> {bas}")
        return "\n".join(lines)


# ── Line allocator ─────────────────────────────────────────────────────────────


class LineAllocator:
    def __init__(self, start=10, step=10):
        if start < 0:
            raise TranspilerError(f"--start must be >= 0 (got {start})")
        if step < 1:
            raise TranspilerError(f"--step must be >= 1 (got {step})")
        if start > MAX_BASIC_LINE:
            raise TranspilerError(
                f"--start {start} exceeds the maximum BASIC line number "
                f"({MAX_BASIC_LINE})"
            )
        self._current = start
        self._step = step

    def next(self):
        n = self._current
        if n > MAX_BASIC_LINE:
            raise TranspilerError(
                f"Program too long: line number {n} exceeds the BASIC maximum "
                f"({MAX_BASIC_LINE}). Use a smaller --step or --start."
            )
        self._current += self._step
        return n

    def peek(self):
        return self._current


# ── Emitter ────────────────────────────────────────────────────────────────────


class Emitter:
    def __init__(self):
        # line number -> statement text. A dict keeps emit() O(1); the previous
        # list-of-pairs made every emit a linear scan, so whole-program
        # emission was quadratic in the number of statements.
        self._lines = {}

    def emit(self, line_num, text):
        self._lines[line_num] = text

    def has_line(self, line_num):
        return line_num in self._lines

    def line_numbers(self):
        return self._lines.keys()

    def texts(self):
        return self._lines.values()

    def output(self):
        return "\n".join(f"{n} {self._lines[n]}" for n in sorted(self._lines))


# ── Dialect code generators ────────────────────────────────────────────────────

# A bare number or BASIC variable reference needs no protective parentheses.
_ATOM_RE = re.compile(r"^(?:\d+(?:\.\d+)?|[A-Z][0-9]?\$?)$")


def _atom(expr: str) -> str:
    """Parenthesise expr unless it is already a single number or variable."""
    return expr if _ATOM_RE.match(expr) else f"({expr})"


def _fake_mod(left: str, right: str) -> str:
    """MOD(a, b) for dialects that lack a MOD() function."""
    a, b = _atom(left), _atom(right)
    return f"{a} - INT({a} / {b}) * {b}"


class DialectEmitter(ABC):
    #: Dialect provides UPPER$() / LOWER$() string-case functions.
    has_string_case = False

    def __init__(self, transpiler):
        self.t = transpiler

    @abstractmethod
    def emit_if(self, node): ...

    @abstractmethod
    def emit_while(self, node): ...

    @abstractmethod
    def emit_break(self, node): ...

    @abstractmethod
    def emit_modulo(self, left, right): ...

    @abstractmethod
    def emit_sleep(self, seconds_expr): ...

    def dialect_name(self):
        return self.__class__.__name__


class Basic2Dialect(DialectEmitter):
    """
    BASIC 2.0 (C64) — the purist experience.

    IF cond:          IF NOT (cond) THEN GOTO else_or_end
        body          ...body...
    else:             GOTO end
        orelse        ...orelse...
                      REM (end)

    while cond:       REM (top)
        body          IF NOT (cond) THEN GOTO end
                      ...body...
                      GOTO top
                      REM (end)

    Modulo:           A - INT(A/B)*B
    sleep():          not available
    """

    def _emit_if_recursive(self, node):
        t = self.t
        cond = t._expr(node.test)
        has_else = bool(node.orelse)

        # Reserve the conditional jump line
        cond_ln = t.lines.next()

        # Emit body statements
        for stmt in node.body:
            t.visit(stmt)

        if not has_else:
            # Simple if: jump past body if condition false
            past_body = t.lines.peek()
            t._note_forward_target(past_body)
            t.emitter.emit(cond_ln, f"IF NOT ({cond}) THEN GOTO {past_body}")
        else:
            # Need a GOTO to skip the else after body executes
            skip_else_ln = t.lines.next()

            if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
                # elif chain
                else_start = t.lines.peek()
                t._note_forward_target(else_start)
                t.emitter.emit(cond_ln, f"IF NOT ({cond}) THEN GOTO {else_start}")
                self._emit_if_recursive(node.orelse[0])
            else:
                # else block
                else_start = t.lines.peek()
                t._note_forward_target(else_start)
                t.emitter.emit(cond_ln, f"IF NOT ({cond}) THEN GOTO {else_start}")
                for stmt in node.orelse:
                    t.visit(stmt)

            # Backfill the skip-else jump
            end_ln = t.lines.peek()
            t._note_forward_target(end_ln)
            t.emitter.emit(skip_else_ln, f"GOTO {end_ln}")

    def emit_if(self, node):
        self._emit_if_recursive(node)

    def emit_while(self, node):
        t = self.t
        # Top of loop — GOTO target
        top_ln = t.lines.next()
        t.emitter.emit(top_ln, "REM")

        cond = t._expr(node.test)

        # Conditional exit placeholder
        cond_ln = t.lines.next()

        # Push break context
        t._break_frames.append({"kind": "while", "lines": []})

        # Emit body
        for stmt in node.body:
            t.visit(stmt)

        # GOTO back to top
        t.emitter.emit(t.lines.next(), f"GOTO {top_ln}")

        # End of loop
        end_ln = t.lines.peek()
        t._note_forward_target(end_ln)
        t.emitter.emit(cond_ln, f"IF NOT ({cond}) THEN GOTO {end_ln}")

        # Backfill breaks
        for bln in t._break_frames.pop()["lines"]:
            t.emitter.emit(bln, f"GOTO {end_ln}")

    def emit_break(self, _node):
        self.t._emit_break_goto()

    def emit_modulo(self, left, right):
        return _fake_mod(left, right)

    def emit_sleep(self, _seconds_expr):
        raise TranspilerError(
            "time.sleep() is not available in BASIC 2.0. "
            "Use a FOR loop delay instead: FOR DL=1 TO 5000:NEXT DL"
        )

    def dialect_name(self):
        return "BASIC 2.0"


class Basic65Dialect(DialectEmitter):
    """
    BASIC 65 (MEGA65) — civilised structured programming.
    BEGIN/BEND, DO WHILE/LOOP, MOD(), SLEEP, UPPER$/LOWER$.
    """

    has_string_case = True

    def _emit_if_recursive(self, node):
        t = self.t
        cond = t._expr(node.test)
        has_else = bool(node.orelse)

        if not has_else:
            t._emit(f"IF {cond} THEN BEGIN")
            for stmt in node.body:
                t.visit(stmt)
            t._emit("BEND")
        elif len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
            t._emit(f"IF {cond} THEN BEGIN")
            for stmt in node.body:
                t.visit(stmt)
            t._emit("BEND ELSE BEGIN")
            self._emit_if_recursive(node.orelse[0])
            t._emit("BEND")
        else:
            t._emit(f"IF {cond} THEN BEGIN")
            for stmt in node.body:
                t.visit(stmt)
            t._emit("BEND ELSE BEGIN")
            for stmt in node.orelse:
                t.visit(stmt)
            t._emit("BEND")

    def emit_if(self, node):
        self._emit_if_recursive(node)

    def emit_while(self, node):
        t = self.t
        cond = t._expr(node.test)
        # DO WHILE <cond> ... LOOP tests at the TOP, matching Python's while.
        # DO ... LOOP WHILE <cond> would be a do-while and always run once.
        t._emit(f"DO WHILE {cond}")
        t._break_frames.append({"kind": "while", "lines": []})
        for stmt in node.body:
            t.visit(stmt)
        t._break_frames.pop()
        t._emit("LOOP")

    def emit_break(self, _node):
        self.t._emit("EXIT")

    def emit_modulo(self, left, right):
        return f"MOD({left}, {right})"

    def emit_sleep(self, seconds_expr):
        self.t._emit(f"SLEEP {seconds_expr}")

    def dialect_name(self):
        return "BASIC 65"


class Basic7Dialect(DialectEmitter):
    """
    BASIC 7.0 (C128) — structured flow, unstructured math.

    Shares BEGIN/BEND IF and DO/LOOP WHILE with BASIC 65.
    No MOD() function — faked with INT() like BASIC 2.0.
    No SLEEP — not available.
    EXIT works for DO/LOOP break.
    """

    def _emit_if_recursive(self, node):
        t = self.t
        cond = t._expr(node.test)
        has_else = bool(node.orelse)

        if not has_else:
            t._emit(f"IF {cond} THEN BEGIN")
            for stmt in node.body:
                t.visit(stmt)
            t._emit("BEND")
        elif len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
            t._emit(f"IF {cond} THEN BEGIN")
            for stmt in node.body:
                t.visit(stmt)
            t._emit("BEND ELSE BEGIN")
            self._emit_if_recursive(node.orelse[0])
            t._emit("BEND")
        else:
            t._emit(f"IF {cond} THEN BEGIN")
            for stmt in node.body:
                t.visit(stmt)
            t._emit("BEND ELSE BEGIN")
            for stmt in node.orelse:
                t.visit(stmt)
            t._emit("BEND")

    def emit_if(self, node):
        self._emit_if_recursive(node)

    def emit_while(self, node):
        t = self.t
        cond = t._expr(node.test)
        # DO WHILE ... LOOP tests at the top; DO ... LOOP WHILE would not.
        t._emit(f"DO WHILE {cond}")
        t._break_frames.append({"kind": "while", "lines": []})
        for stmt in node.body:
            t.visit(stmt)
        t._break_frames.pop()
        t._emit("LOOP")

    def emit_break(self, _node):
        self.t._emit("EXIT")

    def emit_modulo(self, left, right):
        # BASIC 7.0 has no MOD() — fake it like BASIC 2.0
        return _fake_mod(left, right)

    def emit_sleep(self, _seconds_expr):
        raise TranspilerError(
            "time.sleep() is not available in BASIC 7.0. "
            "Use a FOR loop delay instead: FOR DL=1 TO 5000:NEXT DL"
        )

    def dialect_name(self):
        return "BASIC 7.0"


# ── Main transpiler ────────────────────────────────────────────────────────────


class Transpiler(ast.NodeVisitor):
    #: Builtins that always produce a string.
    STRING_CALLS = frozenset({"str", "input", "chr"})
    #: Builtins that always produce a number.
    NUMERIC_CALLS = frozenset(
        {
            "int",
            "float",
            "len",
            "abs",
            "ord",
            "round",
            "sqrt",
            "sin",
            "cos",
            "tan",
            "atan",
            "exp",
            "log",
        }
    )
    #: String methods that produce a string.
    STRING_METHODS = frozenset({"upper", "lower"})

    #: name -> (min_args, max_args, template). "{0}" style placeholders.
    BUILTINS: ClassVar[dict] = {
        "int": (1, 1, "INT({0})"),
        "float": (1, 1, "{0}"),
        "str": (1, 1, "STR$({0})"),
        "abs": (1, 1, "ABS({0})"),
        "len": (1, 1, "LEN({0})"),
        "chr": (1, 1, "CHR$({0})"),
        "ord": (1, 1, "ASC({0})"),
        "round": (1, 1, "INT({0} + 0.5)"),
        "sqrt": (1, 1, "SQR({0})"),
        "sin": (1, 1, "SIN({0})"),
        "cos": (1, 1, "COS({0})"),
        "tan": (1, 1, "TAN({0})"),
        "atan": (1, 1, "ATN({0})"),
        "exp": (1, 1, "EXP({0})"),
        "log": (1, 2, None),  # special-cased: optional base
    }

    def __init__(self, dialect=None):
        self.sym = SymbolTable()
        self.lines = LineAllocator()
        self.emitter = Emitter()
        self._string_vars = set()
        self._functions = {}
        self._gosub_backfills = []
        self._toplevel_defs = set()
        self._break_frames = []
        self._forward_targets = set()
        self._math_aliases = {"math"}
        self.dialect = dialect or Basic2Dialect(self)

    # ── Type inference ─────────────────────────────────────────────────────────

    def _is_string_expr(self, node):
        """True if node provably evaluates to a string."""
        if isinstance(node, ast.Constant):
            return isinstance(node.value, str)
        if isinstance(node, ast.JoinedStr):
            return True
        if isinstance(node, ast.Name):
            return node.id in self._string_vars
        if isinstance(node, ast.BinOp):
            if isinstance(node.op, ast.Add):
                return self._is_string_expr(node.left) or self._is_string_expr(
                    node.right
                )
            if isinstance(node.op, ast.Mod):
                # "fmt %s" % args
                return self._is_string_expr(node.left)
            return False
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                return func.id in self.STRING_CALLS
            if isinstance(func, ast.Attribute):
                return func.attr in self.STRING_METHODS
        return False

    def _is_numeric_expr(self, node):
        """True if node provably evaluates to a number."""
        if isinstance(node, ast.Constant):
            return isinstance(node.value, (int, float, bool))
        if isinstance(node, ast.Compare):
            return True
        if isinstance(node, ast.UnaryOp):
            return self._is_numeric_expr(node.operand)
        if isinstance(node, ast.BinOp):
            if self._is_string_expr(node):
                return False
            return self._is_numeric_expr(node.left) and self._is_numeric_expr(
                node.right
            )
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            return node.func.id in self.NUMERIC_CALLS
        return False

    def _assign_pairs(self, tree):
        """Yield (target_names, value_node, lineno) for every assignment."""
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                names = [t.id for t in node.targets if isinstance(t, ast.Name)]
                if names:
                    yield names, node.value, getattr(node, "lineno", 0)
            elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
                yield [node.target.id], node.value, getattr(node, "lineno", 0)

    def _infer_string_vars(self, tree):
        """
        Work out which Python names hold strings.

        Iterates to a fixpoint: `a = "x"` marks a as a string, which in turn
        lets `b = "y" + a` mark b, and so on. A single pass sees only the
        first of those and would emit `B = "y" + A$` — a numeric variable
        holding a string, which is a ?TYPE MISMATCH ERROR on real hardware.
        """
        pairs = list(self._assign_pairs(tree))

        changed = True
        while changed:
            changed = False
            for names, value, _ in pairs:
                if not self._is_string_expr(value):
                    continue
                for name in names:
                    if name not in self._string_vars:
                        self._string_vars.add(name)
                        changed = True

        # Catch variables used as both — BASIC has no such thing.
        for names, value, lineno in pairs:
            for name in names:
                if name in self._string_vars and self._is_numeric_expr(value):
                    raise TranspilerError(
                        f"Variable '{name}' is assigned both string and numeric "
                        f"values. BASIC variables have a fixed type — use two "
                        f"separate names.",
                        lineno,
                    )

    # ── Forward jump bookkeeping ───────────────────────────────────────────────

    def _note_forward_target(self, line_num):
        """Record a GOTO target that has not been emitted yet."""
        self._forward_targets.add(line_num)

    def _emit_landing_pads(self):
        """
        BASIC 2.0 leaves a construct by jumping to "the line after it". When
        the construct is the last thing in the program that line never gets
        emitted, and the machine raises ?UNDEF'D STATEMENT ERROR at runtime.
        Emit a landing line for any target that ended up dangling.
        """
        if not self._forward_targets:
            return
        highest = max(self.emitter.line_numbers(), default=0)
        for target in sorted(self._forward_targets):
            if not self.emitter.has_line(target):
                self.emitter.emit(target, "END" if target > highest else "REM")

    def _emit_break_goto(self):
        """Reserve a line for a `break` and register it for backfilling."""
        ln = self.lines.next()
        self.emitter.emit(ln, "GOTO 0")  # placeholder, backfilled by the loop
        self._break_frames[-1]["lines"].append(ln)

    def _basic_var(self, py_name):
        if py_name in self._string_vars:
            return self.sym.get_string(py_name)
        return self.sym.get_numeric(py_name)

    def _escape_commodore_string(self, s: str) -> str:
        """
        Convert a Python string containing double quotes into a BASIC
        string expression using CHR$(34) for the quotes.

        Returns a complete BASIC expression (not just the inner part),
        so callers must NOT wrap it in additional quotes.

        Examples:
          'hello'          -> '"hello"'
          'say "hi"'       -> '"say " + CHR$(34) + "hi" + CHR$(34)'
          '"quoted"'       -> 'CHR$(34) + "quoted" + CHR$(34)'
        """
        if '"' not in s:
            return f'"{s}"'
        # Split on embedded quotes, filter empty segments, join with CHR$(34)
        segments = s.split('"')
        parts = []
        for i, seg in enumerate(segments):
            if seg:
                parts.append(f'"{seg}"')
            if i < len(segments) - 1:
                parts.append("CHR$(34)")
        return " + ".join(parts) if parts else '""'

    # ── Expression compiler ────────────────────────────────────────────────────

    def _expr(self, node):
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                return "-1" if node.value else "0"
            if isinstance(node.value, (int, float)):
                return str(node.value)
            if isinstance(node.value, str):
                return self._escape_commodore_string(node.value)
            raise TranspilerError(
                f"Unsupported constant: {type(node.value)}", getattr(node, "lineno", 0)
            )

        if isinstance(node, ast.Name):
            if node.id == "True":
                return "-1"
            if node.id == "False":
                return "0"
            return self._basic_var(node.id)

        if isinstance(node, ast.BinOp):
            op = node.op
            # String % formatting: "Hello %s" % name  or  "Hi %s %s" % (a, b)
            # Must check before evaluating left/right so we can inspect AST types.
            if (
                isinstance(op, ast.Mod)
                and isinstance(node.left, ast.Constant)
                and isinstance(node.left.value, str)
            ):
                return self._expand_percent_format(
                    node.left.value, node.right, getattr(node, "lineno", 0)
                )
            left = self._expr(node.left)
            right = self._expr(node.right)
            if isinstance(op, ast.Add):
                return f"{left} + {right}"
            if isinstance(op, ast.Sub):
                return f"{left} - {right}"
            if isinstance(op, ast.Mult):
                return f"{left} * {right}"
            if isinstance(op, ast.Div):
                return f"{left} / {right}"
            if isinstance(op, ast.FloorDiv):
                return f"INT({left} / {right})"
            if isinstance(op, ast.Mod):
                return self.dialect.emit_modulo(left, right)
            if isinstance(op, ast.Pow):
                return f"{left} ^ {right}"
            if isinstance(op, ast.BitAnd):
                return f"({left}) AND ({right})"
            if isinstance(op, ast.BitOr):
                return f"({left}) OR ({right})"
            if isinstance(op, ast.BitXor):
                return f"({left}) XOR ({right})"
            raise TranspilerError(
                f"Unsupported operator: {type(op).__name__}", getattr(node, "lineno", 0)
            )

        if isinstance(node, ast.UnaryOp):
            operand = self._expr(node.operand)
            if isinstance(node.op, ast.USub):
                return f"-{_atom(operand)}"
            if isinstance(node.op, ast.UAdd):
                return operand
            if isinstance(node.op, ast.Not):
                return f"NOT ({operand})"
            raise TranspilerError(
                f"Unsupported unary op: {type(node.op).__name__}",
                getattr(node, "lineno", 0),
            )

        if isinstance(node, ast.BoolOp):
            op = "AND" if isinstance(node.op, ast.And) else "OR"
            parts = [f"({self._expr(v)})" for v in node.values]
            return f" {op} ".join(parts)

        if isinstance(node, ast.Compare):
            if len(node.ops) != 1:
                raise TranspilerError(
                    "Chained comparisons not supported", getattr(node, "lineno", 0)
                )
            left = self._expr(node.left)
            right = self._expr(node.comparators[0])
            op_map = {
                ast.Eq: "=",
                ast.NotEq: "<>",
                ast.Lt: "<",
                ast.LtE: "<=",
                ast.Gt: ">",
                ast.GtE: ">=",
            }
            basic_op = op_map.get(type(node.ops[0]))
            if not basic_op:
                raise TranspilerError(
                    f"Unsupported comparison: {type(node.ops[0]).__name__}",
                    getattr(node, "lineno", 0),
                )
            return f"{left} {basic_op} {right}"

        if isinstance(node, ast.Call):
            return self._call_expr(node)

        if isinstance(node, ast.IfExp):
            raise TranspilerError(
                "Ternary expressions not supported.", getattr(node, "lineno", 0)
            )

        if isinstance(node, ast.JoinedStr):
            return self._expand_fstring(node)

        raise TranspilerError(
            f"Unsupported expression: {type(node).__name__}", getattr(node, "lineno", 0)
        )

    def _expand_percent_format(self, fmt: str, args_node, lineno: int) -> str:
        """
        Expand Python % string formatting into BASIC string concatenation.
        "Hello %s, you are %d" % (name, age)
        -> "Hello " + A$ + ", you are " + STR$(B) (joined with ; in PRINT context)

        Supported: %s, %d, %i, %f, %g
        (all become STR$() for numeric, direct for strings)
        Not supported: width/precision specifiers like %-10s, %05d, etc.
        """
        # Collect the argument nodes into a list
        if isinstance(args_node, ast.Tuple):
            arg_nodes = list(args_node.elts)
        else:
            arg_nodes = [args_node]

        # Split format string on % specifiers. %% must be in the pattern too,
        # otherwise it stays glued to its neighbours and reaches the output
        # as a literal "%%".
        parts = re.split(r"(%%|%[sdifrg])", fmt)

        result_parts = []
        arg_index = 0

        for part in parts:
            if re.match(r"^%[sdifrg]$", part):
                # A format specifier — consume next argument
                if arg_index >= len(arg_nodes):
                    raise TranspilerError(
                        f"Too few arguments for format string '{fmt}'", lineno
                    )
                arg_node = arg_nodes[arg_index]
                arg_index += 1
                spec = part[1]  # the letter after %

                if spec == "s":
                    # String: if already a string type, use directly; else STR$()
                    is_str = (
                        isinstance(arg_node, ast.Constant)
                        and isinstance(arg_node.value, str)
                    ) or (
                        isinstance(arg_node, ast.Name)
                        and arg_node.id in self._string_vars
                    )
                    if is_str:
                        result_parts.append(self._expr(arg_node))
                    else:
                        result_parts.append(f"STR$({self._expr(arg_node)})")
                else:
                    # Numeric (%d, %i, %f, %g): use STR$() to convert to string
                    if spec in ("d", "i"):
                        result_parts.append(f"STR$(INT({self._expr(arg_node)}))")
                    else:
                        result_parts.append(f"STR$({self._expr(arg_node)})")
            elif part == "%%":
                result_parts.append('"%"')
            elif part:
                result_parts.append(self._escape_commodore_string(part))

        if arg_index < len(arg_nodes):
            raise TranspilerError(
                f"Too many arguments for format string '{fmt}'", lineno
            )

        if not result_parts:
            return '""'

        return " + ".join(result_parts)

    def _expand_fstring(self, node) -> str:
        """
        Expand an f-string (JoinedStr) into BASIC string concatenation.

        f"Hello {name}, age {age}"
        -> "Hello " + A$ + ", age " + STR$(A)

        Supported:
          {expr}        plain expression
          {expr!s}      str() conversion — same as plain for us
          {expr!r}      repr() — treated same as str(), no quotes added
        Not supported:
          {expr:.2f}    format specs — rejected with helpful error
          {expr!a}      ascii conversion
        """
        lineno = getattr(node, "lineno", 0)
        parts = []

        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                # Literal string segment
                if value.value:
                    parts.append(self._escape_commodore_string(value.value))
            elif isinstance(value, ast.FormattedValue):
                # Check for unsupported format specs
                if value.format_spec is not None:
                    raise TranspilerError(
                        "f-string format specs (e.g. {x:.2f}) are not supported. "
                        "Use str() or int() to convert manually.",
                        lineno,
                    )
                # !a conversion not supported
                if value.conversion == 97:  # 'a'
                    raise TranspilerError(
                        "f-string !a conversion not supported.", lineno
                    )

                expr = self._expr(value.value)

                # Determine if the expression is already a string
                is_str = (
                    (
                        isinstance(value.value, ast.Constant)
                        and isinstance(value.value.value, str)
                    )
                    or (
                        isinstance(value.value, ast.Name)
                        and value.value.id in self._string_vars
                    )
                    or (
                        isinstance(value.value, ast.Call)
                        and isinstance(value.value.func, ast.Name)
                        and value.value.func.id in {"str", "chr", "input"}
                    )
                )

                if is_str:
                    parts.append(expr)
                else:
                    parts.append(f"STR$({expr})")
            else:
                raise TranspilerError(
                    f"Unsupported f-string component: {type(value).__name__}", lineno
                )

        if not parts:
            return '""'
        return " + ".join(parts)

    def _check_call(self, name, node, lineno):
        """Validate argument count and reject keyword arguments."""
        low, high, _ = self.BUILTINS[name]
        if node.keywords:
            raise TranspilerError(
                f"'{name}()' does not accept keyword arguments here.", lineno
            )
        count = len(node.args)
        if not low <= count <= high:
            want = str(low) if low == high else f"{low}-{high}"
            raise TranspilerError(
                f"'{name}()' takes {want} argument(s), got {count}.", lineno
            )

    def _builtin_expr(self, name, node, lineno):
        self._check_call(name, node, lineno)
        args = [self._expr(a) for a in node.args]
        if name == "log":
            if len(args) == 2:
                return f"(LOG({args[0]}) / LOG({args[1]}))"
            return f"LOG({args[0]})"
        return self.BUILTINS[name][2].format(*args)

    def _call_expr(self, node):
        lineno = getattr(node, "lineno", 0)

        if isinstance(node.func, ast.Name):
            name = node.func.id
            if name in self.BUILTINS:
                return self._builtin_expr(name, node, lineno)
            if name == "input":
                raise TranspilerError("input() can't be used as expression.", lineno)
            if name in self._functions:
                raise TranspilerError(
                    f"'{name}()' is a subroutine with no return value.", lineno
                )
            raise TranspilerError(
                f"Unsupported function in expression: '{name}()'", lineno
            )

        if isinstance(node.func, ast.Attribute):
            obj = node.func.value
            attr = node.func.attr
            if isinstance(obj, ast.Name):
                # math.sqrt(x) and friends — same table as the bare names.
                if obj.id in self._math_aliases:
                    if attr in self.BUILTINS:
                        return self._builtin_expr(attr, node, lineno)
                    raise TranspilerError(
                        f"math.{attr}() has no BASIC equivalent.", lineno
                    )
                if attr in self.STRING_METHODS:
                    if not self.dialect.has_string_case:
                        raise TranspilerError(
                            f".{attr}() needs UPPER$/LOWER$, which "
                            f"{self.dialect.dialect_name()} does not have.",
                            lineno,
                        )
                    fn = "UPPER$" if attr == "upper" else "LOWER$"
                    return f"{fn}({self._expr(obj)})"
            raise TranspilerError(f"Unsupported method: .{node.func.attr}()", lineno)

        raise TranspilerError(f"Unsupported call: {ast.dump(node)}", lineno)

    # ── Emitter helpers ────────────────────────────────────────────────────────

    def _emit(self, text):
        ln = self.lines.next()
        self.emitter.emit(ln, text)
        return ln

    def _emit_at(self, line_num, text):
        self.emitter.emit(line_num, text)

    # ── Statement visitors ─────────────────────────────────────────────────────

    def visit_Module(self, node):
        self._infer_string_vars(node)

        top_defs = [s for s in node.body if isinstance(s, ast.FunctionDef)]
        has_functions = bool(top_defs)

        # Pre-register functions (placeholder=0) so call sites can find them.
        # Only top-level defs qualify; anything nested is rejected in
        # visit_FunctionDef rather than crashing with a KeyError.
        for fn in top_defs:
            if fn.name in self._functions:
                raise TranspilerError(
                    f"Function '{fn.name}' is defined more than once.", fn.lineno
                )
            self._functions[fn.name] = 0
            self._toplevel_defs.add(id(fn))

        # Emit main body
        for stmt in node.body:
            if not isinstance(stmt, ast.FunctionDef):
                self.visit(stmt)

        if has_functions:
            # END separates main code from subroutines below it - no GOTO needed
            self._emit("END")

            # Emit each subroutine completely before moving to the next,
            # assigning its line number immediately before emitting its body.
            # This prevents interleaving when multiple functions are defined.
            for stmt in node.body:
                if isinstance(stmt, ast.FunctionDef):
                    self._functions[stmt.name] = self.lines.next()
                    self.visit(stmt)

            # Backfill all GOSUB placeholders now that line numbers are known
            for gosub_ln, func_name in self._gosub_backfills:
                self.emitter.emit(gosub_ln, f"GOSUB {self._functions[func_name]}")

        self._emit_landing_pads()

    def visit_FunctionDef(self, node):
        lineno = node.lineno
        if id(node) not in self._toplevel_defs:
            raise TranspilerError(
                f"Function '{node.name}' is not defined at module level. "
                f"BASIC subroutines cannot be nested or conditional.",
                lineno,
            )
        if node.args.args or node.args.vararg or node.args.kwarg:
            raise TranspilerError(
                f"Function '{node.name}' has parameters. "
                f"BASIC subroutines cannot take parameters. "
                f"Use global variables instead.",
                lineno,
            )

        func_ln = self._functions[node.name]
        self._emit_at(func_ln, f"REM -- {node.name}")

        for stmt in node.body:
            self.visit(stmt)

        # Only emit trailing RETURN if the function doesn't already end with one
        last_stmt = node.body[-1] if node.body else None
        if not isinstance(last_stmt, ast.Return):
            self._emit("RETURN")

    def visit_Return(self, node):
        if node.value is not None:
            raise TranspilerError(
                "return <value> not supported. Store results in a variable instead.",
                node.lineno,
            )
        self._emit("RETURN")

    def _const_str_kwarg(self, kw, lineno):
        if not (isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str)):
            raise TranspilerError(
                f"print({kw.arg}=...) must be a plain string literal.", lineno
            )
        return kw.value.value

    def _emit_print(self, node, lineno):
        """Compile print(), including its sep= and end= keywords."""
        sep = " ; "
        suppress_newline = False

        for kw in node.keywords:
            if kw.arg == "sep":
                text = self._const_str_kwarg(kw, lineno)
                if text:
                    sep = f" ; {self._escape_commodore_string(text)} ; "
            elif kw.arg == "end":
                text = self._const_str_kwarg(kw, lineno)
                if text == "":
                    suppress_newline = True
                elif text != "\n":
                    raise TranspilerError(
                        "print(end=...) supports only '' and '\\n'.", lineno
                    )
            elif kw.arg is None:
                raise TranspilerError("print(**kwargs) is not supported.", lineno)
            else:
                raise TranspilerError(f"print({kw.arg}=...) is not supported.", lineno)

        parts = [self._expr(a) for a in node.args]
        stmt = "PRINT" if not parts else f"PRINT {sep.join(parts)}"
        if suppress_newline:
            # A trailing ; leaves the cursor where it is, like end="".
            stmt += ";"
        self._emit(stmt)

    def _compile_call_stmt(self, node):
        lineno = getattr(node, "lineno", 0)

        if isinstance(node.func, ast.Name):
            name = node.func.id

            if name == "print":
                self._emit_print(node, lineno)
                return

            if name == "input":
                raise TranspilerError("Use: var = input('prompt')", lineno)

            if name == "sleep":
                if len(node.args) != 1:
                    raise TranspilerError("sleep() takes exactly 1 argument.", lineno)
                self.dialect.emit_sleep(self._expr(node.args[0]))
                return

            if name == "exit":
                self._emit("END")
                return

            if name in self._functions:
                ln = self.lines.next()
                self.emitter.emit(ln, "GOSUB 0")
                self._gosub_backfills.append((ln, name))
                return

            raise TranspilerError(f"Unknown function: '{name}()'", lineno)

        if isinstance(node.func, ast.Attribute):
            obj = node.func.value
            attr = node.func.attr
            if isinstance(obj, ast.Name):
                if obj.id == "time" and attr == "sleep":
                    if len(node.args) != 1:
                        raise TranspilerError(
                            "time.sleep() takes exactly 1 argument.", lineno
                        )
                    self.dialect.emit_sleep(self._expr(node.args[0]))
                    return
                if obj.id == "sys" and attr == "exit":
                    self._emit("END")
                    return
            raise TranspilerError(f"Unsupported method call: {ast.dump(node)}", lineno)

        raise TranspilerError(f"Unsupported call: {ast.dump(node)}", lineno)

    def visit_Expr(self, node):
        if isinstance(node.value, ast.Call):
            self._compile_call_stmt(node.value)
        elif isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            # Docstrings and bare string literals — treat as REM comments.
            # Take only the first line since BASIC lines can't span multiple lines.
            first_line = (
                node.value.value.strip().splitlines()[0]
                if node.value.value.strip()
                else ""
            )
            if first_line:
                self._emit(f"REM {first_line}")
            # else: empty docstring, emit nothing
        else:
            raise TranspilerError(
                f"Bare expression not supported: {ast.dump(node.value)}", node.lineno
            )

    def visit_Assign(self, node):
        if len(node.targets) != 1:
            raise TranspilerError(
                "Multiple assignment targets not supported", node.lineno
            )
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            raise TranspilerError(
                "Only simple variable assignment supported", node.lineno
            )

        py_name = target.id
        value = node.value

        # var = input("prompt")
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "input"
        ):
            prompt = self._expr(value.args[0]) if value.args else None
            basic_var = self._basic_var(py_name)
            if prompt:
                self._emit(f"PRINT {prompt};")
            self._emit(f"INPUT {basic_var}")
            return

        basic_var = self._basic_var(py_name)
        self._emit(f"{basic_var} = {self._expr(value)}")

    def visit_AugAssign(self, node):
        if not isinstance(node.target, ast.Name):
            raise TranspilerError(
                "Augmented assignment only for simple variables", node.lineno
            )
        py_name = node.target.id
        basic_var = self._basic_var(py_name)
        rhs = self._expr(node.value)
        op = node.op

        if isinstance(op, ast.Mod):
            self._emit(f"{basic_var} = {self.dialect.emit_modulo(basic_var, rhs)}")
        elif isinstance(op, ast.FloorDiv):
            self._emit(f"{basic_var} = INT({basic_var} / {rhs})")
        else:
            op_map = {
                ast.Add: "+",
                ast.Sub: "-",
                ast.Mult: "*",
                ast.Div: "/",
                ast.Pow: "^",
            }
            basic_op = op_map.get(type(op))
            if not basic_op:
                raise TranspilerError(
                    f"Unsupported augmented operator: {type(op).__name__}", node.lineno
                )
            self._emit(f"{basic_var} = {basic_var} {basic_op} {rhs}")

    def visit_If(self, node):
        self.dialect.emit_if(node)

    def visit_While(self, node):
        if node.orelse:
            raise TranspilerError("while/else not supported", node.lineno)
        self.dialect.emit_while(node)

    @staticmethod
    def _static_num(node):
        """Literal numeric value of node, or None. Sees through unary +/-."""
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                return None
            return node.value
        if isinstance(node, ast.UnaryOp):
            inner = Transpiler._static_num(node.operand)
            if inner is None:
                return None
            if isinstance(node.op, ast.USub):
                return -inner
            if isinstance(node.op, ast.UAdd):
                return inner
        return None

    def visit_For(self, node):
        lineno = node.lineno
        if node.orelse:
            raise TranspilerError("for/else not supported", lineno)
        if not isinstance(node.target, ast.Name):
            raise TranspilerError("for loop target must be a simple variable", lineno)
        if not (
            isinstance(node.iter, ast.Call)
            and isinstance(node.iter.func, ast.Name)
            and node.iter.func.id == "range"
        ):
            raise TranspilerError("Only range() is supported in for loops.", lineno)

        py_var = node.target.id
        if py_var in self._string_vars:
            raise TranspilerError(
                f"Loop variable '{py_var}' was used as a string elsewhere", lineno
            )
        basic_var = self.sym.get_numeric(py_var)

        args = node.iter.args
        if node.iter.keywords:
            raise TranspilerError("range() takes no keyword arguments", lineno)
        if not 1 <= len(args) <= 3:
            raise TranspilerError("range() takes 1-3 arguments", lineno)

        if len(args) == 1:
            start_node, stop_node, step_node = None, args[0], None
        elif len(args) == 2:
            start_node, stop_node, step_node = args[0], args[1], None
        else:
            start_node, stop_node, step_node = args

        # The loop direction has to be known now: it decides both which way
        # the inclusive TO bound is adjusted and which way the zero-trip
        # guard compares. Note that -1 parses as UnaryOp(USub), never as a
        # Constant, so _static_num has to see through that.
        step_val = 1
        step_expr = None
        if step_node is not None:
            step_val = self._static_num(step_node)
            if step_val is None:
                raise TranspilerError(
                    "range() step must be a literal number so the loop "
                    "direction is known at transpile time.",
                    lineno,
                )
            if step_val == 0:
                raise TranspilerError("range() step must not be zero", lineno)
            step_expr = self._expr(step_node)

        start_expr = "0" if start_node is None else self._expr(start_node)
        start_val = 0 if start_node is None else self._static_num(start_node)

        # Python's stop is exclusive, BASIC's TO bound is inclusive.
        delta = -1 if step_val > 0 else 1
        stop_val = self._static_num(stop_node)
        if stop_val is None:
            sign = "-" if delta < 0 else "+"
            stop_expr = f"{_atom(self._expr(stop_node))} {sign} 1"
        else:
            stop_val += delta
            stop_expr = str(stop_val)

        # Commodore BASIC tests the loop bound at NEXT, so FOR I = 0 TO -1
        # runs the body once. Python's range() would not run at all.
        if start_val is not None and stop_val is not None:
            empty = start_val > stop_val if step_val > 0 else start_val < stop_val
            if empty:
                self._emit(f"REM EMPTY RANGE -- {py_var} LOOP SKIPPED")
                return
            guard_ln = None
        else:
            # Bounds unknown until runtime — reserve a line to skip the loop.
            guard_ln = self.lines.next()

        header = f"FOR {basic_var} = {start_expr} TO {stop_expr}"
        if step_expr:
            header += f" STEP {step_expr}"
        self._emit(header)

        self._break_frames.append({"kind": "for", "lines": []})
        for stmt in node.body:
            self.visit(stmt)
        frame = self._break_frames.pop()

        self._emit(f"NEXT {basic_var}")

        after_ln = self.lines.peek()
        if guard_ln is not None or frame["lines"]:
            self._note_forward_target(after_ln)
        if guard_ln is not None:
            skip_op = ">" if step_val > 0 else "<"
            self._emit_at(
                guard_ln,
                f"IF {start_expr} {skip_op} {stop_expr} THEN GOTO {after_ln}",
            )
        for break_ln in frame["lines"]:
            self._emit_at(break_ln, f"GOTO {after_ln}")

    def visit_Break(self, node):
        if not self._break_frames:
            raise TranspilerError("break outside loop", node.lineno)
        if self._break_frames[-1]["kind"] == "for":
            # EXIT leaves a DO loop, not a FOR loop, in every dialect here —
            # so jump past the NEXT instead.
            self._emit_break_goto()
        else:
            self.dialect.emit_break(node)

    def visit_Continue(self, node):
        raise TranspilerError(
            "continue not supported. Restructure using an if/else guard.", node.lineno
        )

    def visit_Pass(self, _node):
        self._emit("REM")

    def visit_Import(self, node):
        for alias in node.names:
            if alias.name not in {"time", "sys", "math"}:
                raise TranspilerError(
                    f"import {alias.name} not supported.", node.lineno
                )
            if alias.name == "math":
                self._math_aliases.add(alias.asname or "math")

    def visit_ImportFrom(self, node):
        if node.module not in {"time", "sys", "math"}:
            raise TranspilerError(
                f"from {node.module} import ... not supported.", node.lineno
            )

    def visit_Global(self, node):
        raise TranspilerError(
            "global not needed — all BASIC variables are global.", node.lineno
        )

    def visit_Nonlocal(self, node):
        raise TranspilerError("nonlocal not supported.", node.lineno)

    def visit_Delete(self, node):
        raise TranspilerError("del not supported.", node.lineno)

    def visit_Assert(self, node):
        cond = self._expr(node.test)
        msg = self._expr(node.msg) if node.msg else '"ASSERTION FAILED"'
        # Inline assert: reserve lines for the check, print, end
        check_ln = self.lines.next()
        print_ln = self.lines.next()
        end_ln = self.lines.next()
        skip_ln = self.lines.peek()
        self._note_forward_target(skip_ln)
        self.emitter.emit(check_ln, f"IF ({cond}) THEN GOTO {skip_ln}")
        self.emitter.emit(print_ln, f"PRINT {msg}")
        self.emitter.emit(end_ln, "END")

    def visit_ClassDef(self, node):
        raise TranspilerError("Classes not supported.", node.lineno)

    def visit_Try(self, node):
        raise TranspilerError("try/except not supported.", node.lineno)

    def visit_With(self, node):
        raise TranspilerError("with statements not supported.", node.lineno)

    def visit_Raise(self, node):
        raise TranspilerError("raise not supported.", node.lineno)

    def generic_visit(self, node):
        raise TranspilerError(
            f"Unsupported Python construct: {type(node).__name__}",
            getattr(node, "lineno", 0),
        )

    def transpile(self, source):
        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            raise TranspilerError(f"Python syntax error: {e}") from e
        self.visit(tree)
        return self.emitter.output()


# ── CLI ────────────────────────────────────────────────────────────────────────


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="py2basic — Transpile Python to Commodore BASIC",
        epilog=textwrap.dedent("""
        Dialect flags (mutually exclusive):
          (default)  BASIC 2.0  C64
          --basic65  BASIC 65   MEGA65
          --basic7   BASIC 7.0  C128

        Examples:
          python3 transpiler.py hello.py              # BASIC 2.0 (default)
          python3 transpiler.py hello.py --basic65    # MEGA65 BASIC 65
          python3 transpiler.py hello.py -o out.bas --vars
        """),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input", help="Python source file")
    parser.add_argument("-o", "--output", help="Output file (default: stdout)")
    parser.add_argument(
        "--vars", action="store_true", help="Print variable name mapping to stderr"
    )
    parser.add_argument(
        "--start", type=int, default=10, help="First BASIC line number (default: 10)"
    )
    parser.add_argument(
        "--step", type=int, default=10, help="Line number increment (default: 10)"
    )

    dgroup = parser.add_mutually_exclusive_group()
    dgroup.add_argument(
        "--basic2", action="store_true", help="BASIC 2.0 / C64 (default)"
    )
    dgroup.add_argument("--basic65", action="store_true", help="BASIC 65 / MEGA65")
    dgroup.add_argument("--basic7", action="store_true", help="BASIC 7.0 / C128")

    args = parser.parse_args()

    if args.step < 1:
        parser.error(
            f"--step must be at least 1 (got {args.step}); BASIC line numbers "
            f"have to increase"
        )
    if not 0 <= args.start <= MAX_BASIC_LINE:
        parser.error(f"--start must be between 0 and {MAX_BASIC_LINE}")

    try:
        with open(args.input) as f:
            source = f.read()
    except FileNotFoundError:
        print(f"Error: file not found: {args.input}", file=sys.stderr)
        sys.exit(1)
    except OSError as e:
        print(f"Error: cannot read {args.input}: {e}", file=sys.stderr)
        sys.exit(1)

    dialect_cls = (
        Basic65Dialect
        if args.basic65
        else Basic7Dialect if args.basic7 else Basic2Dialect
    )

    try:
        t = Transpiler()
        t.lines = LineAllocator(args.start, args.step)
        t.dialect = dialect_cls(t)
        print(f"-- Dialect: {t.dialect.dialect_name()}", file=sys.stderr)
        result = t.transpile(source)
    except TranspilerError as e:
        print(f"Transpiler error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.output:
        try:
            with open(args.output, "w") as f:
                f.write(result + "\n")
        except OSError as e:
            print(f"Error: cannot write {args.output}: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"Written to {args.output}", file=sys.stderr)
    else:
        print(result)

    if args.vars:
        print("\n" + t.sym.dump(), file=sys.stderr)


if __name__ == "__main__":
    main()
