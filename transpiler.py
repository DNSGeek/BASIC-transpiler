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
  - def f(x): return <expr>     -> DEF FN
  - Basic math (+,-,*,/,%,**)   -> BASIC ops
  - lists of fixed size         -> DIM arrays
  - string slicing              -> LEFT$ / RIGHT$ / MID$
  - break / continue            -> EXIT or GOTO
  - time.sleep() / sys.exit()   -> SLEEP (B65, whole seconds on B7.0) / END
  - assert                      -> IF NOT / PRINT / END

NOT supported:
  - Function parameters or return values (beyond DEF FN)
  - dicts, sets, tuples
  - Classes, lambda, comprehensions
  - import (except time, sys, math, random, py2basic_runtime)
  - try/except, with, raise
  - Tuple unpacking (a, b = 1, 2)
"""

import ast
import math
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
        self._num_array: dict = {}
        self._str_array: dict = {}
        self._functions: dict = {}
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

    def get_array(self, py_name, is_string):
        """
        Arrays live in their own namespace: in Commodore BASIC A and A(0)
        are different variables, so an array may reuse a scalar's letter.
        """
        table = self._str_array if is_string else self._num_array
        if py_name not in table:
            if len(table) >= 286:
                raise TranspilerError(
                    f"Too many arrays (max 286). Overflow on '{py_name}'"
                )
            n = len(table)
            if n < 26:
                base = self.LETTERS[n]
            else:
                n -= 26
                base = self.LETTERS[n // 10] + str(n % 10)
            table[py_name] = base + "$" if is_string else base
        return table[py_name]

    def get_function(self, py_name):
        """DEF FN names. FN plus a variable name, e.g. FNA."""
        if py_name not in self._functions:
            n = len(self._functions)
            if n >= 286:
                raise TranspilerError(
                    f"Too many DEF FN functions (max 286). Overflow on '{py_name}'"
                )
            if n < 26:
                self._functions[py_name] = "FN" + self.LETTERS[n]
            else:
                n -= 26
                self._functions[py_name] = "FN" + self.LETTERS[n // 10] + str(n % 10)
        return self._functions[py_name]

    def dump(self):
        lines = ["Variable map:"]
        for py, bas in sorted(self._numeric.items()):
            lines.append(f"  {py:20s} -> {bas}")
        for py, bas in sorted(self._string.items()):
            lines.append(f"  {py:20s} -> {bas}")
        for py, bas in sorted({**self._num_array, **self._str_array}.items()):
            lines.append(f"  {py:20s} -> {bas}()")
        for py, bas in sorted(self._functions.items()):
            lines.append(f"  {py:20s} -> {bas}()")
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


# Precedence of the BASIC text _expr() produces, highest first. BASIC and
# Python agree on the order, but BASIC's ^ is left-associative where
# Python's ** is right-associative, and BASIC's NOT/AND/OR are bitwise.
_PREC_ATOM = 10
_PREC_POW = 7
_PREC_NEG = 6
_PREC_MUL = 5
_PREC_ADD = 4
_PREC_CMP = 3
_PREC_NOT = 2
_PREC_AND = 1
_PREC_OR = 0

_BINOP_PREC = {
    ast.Pow: _PREC_POW,
    ast.Mult: _PREC_MUL,
    ast.Div: _PREC_MUL,
    ast.Add: _PREC_ADD,
    ast.Sub: _PREC_ADD,
}


class DialectEmitter(ABC):
    #: Dialect provides UPPER$() / LOWER$() string-case functions.
    has_string_case = False
    #: Dialect provides INSTR() for substring search.
    has_instr = False
    #: Dialect provides HEX$() and DEC().
    has_hex = False
    #: Dialect provides a MOD() function (otherwise it is faked with INT()).
    has_mod = False
    #: Dialect provides the XOR(a, b) function.
    has_xor = False
    #: Longest program line the machine's screen editor accepts.
    max_line_length = 80
    #: Empty FOR/NEXT iterations per second, used to fake time.sleep().
    #: Rough — real timing depends on machine, ROM and video standard.
    delay_loop_rate = 1000

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

    while cond:       IF NOT (cond) THEN GOTO end     (top)
        body          ...body...
                      GOTO top
                      (end)

    while True:       ...body...                      (top)
        body          GOTO top
                      (end)

    Modulo:           A - INT(A/B)*B
    sleep():          FOR/NEXT delay loop
    """

    def _skip_test(self, node):
        """
        The condition under which a construct guarded by node is skipped.

        `IF NOT (cond)` in general; `if not x:` and `while not done:` drop
        the double negative and become `IF (x)`.
        """
        t = self.t
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return f"({t._truth(node.operand)})"
        return f"NOT ({t._truth(node)})"

    def _emit_if_recursive(self, node):
        t = self.t
        skip = self._skip_test(node.test)
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
            t.emitter.emit(cond_ln, f"IF {skip} THEN GOTO {past_body}")
        else:
            # Need a GOTO to skip the else after body executes
            skip_else_ln = t.lines.next()

            else_start = t.lines.peek()
            t._note_forward_target(else_start)
            t.emitter.emit(cond_ln, f"IF {skip} THEN GOTO {else_start}")
            if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
                # elif chain
                self._emit_if_recursive(node.orelse[0])
            else:
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
        skip = None
        if t._is_true_const(node.test):
            # while True: has no exit test, so the first body line is the
            # top of the loop; only break leaves it.
            top_ln = t.lines.peek()
            cond_ln = None
        else:
            # The conditional exit is itself the top of the loop, so the
            # back edge and continue jump straight to it.
            skip = self._skip_test(node.test)
            top_ln = cond_ln = t.lines.next()

        # Push break context
        t._break_frames.append({"kind": "while", "lines": [], "continues": []})

        # Emit body
        for stmt in node.body:
            t.visit(stmt)

        # GOTO back to top
        t.emitter.emit(t.lines.next(), f"GOTO {top_ln}")

        # End of loop
        end_ln = t.lines.peek()
        t._note_forward_target(end_ln)
        if cond_ln is not None:
            t.emitter.emit(cond_ln, f"IF {skip} THEN GOTO {end_ln}")

        frame = t._break_frames.pop()
        for bln in frame["lines"]:
            t.emitter.emit(bln, f"GOTO {end_ln}")
        # continue re-tests the condition, so it jumps to the loop top.
        for cln in frame["continues"]:
            t.emitter.emit(cln, f"GOTO {top_ln}")

    def emit_break(self, _node):
        self.t._emit_break_goto()

    def emit_modulo(self, left, right):
        return _fake_mod(left, right)

    def emit_sleep(self, seconds_expr):
        self.t.emit_delay_loop(seconds_expr)

    def dialect_name(self):
        return "BASIC 2.0"


class _StructuredDialect(DialectEmitter):
    """
    What BASIC 7.0 and BASIC 65 share: BEGIN/BEND blocks, DO/LOOP with
    EXIT, INSTR, HEX$/DEC and the XOR() function.
    """

    has_instr = True
    has_hex = True
    has_xor = True
    #: The C128 and MEGA65 editors take 160-character lines.
    max_line_length = 160

    def _emit_if_recursive(self, node):
        t = self.t
        t._emit(f"IF {t._truth(node.test)} THEN BEGIN")
        for stmt in node.body:
            t.visit(stmt)
        if not node.orelse:
            t._emit("BEND")
            return
        # ELSE must follow BEND on the same line, after a colon.
        t._emit("BEND : ELSE BEGIN")
        if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
            self._emit_if_recursive(node.orelse[0])
        else:
            for stmt in node.orelse:
                t.visit(stmt)
        t._emit("BEND")

    def emit_if(self, node):
        self._emit_if_recursive(node)

    def emit_while(self, node):
        t = self.t
        if t._is_true_const(node.test):
            # while True: is a bare DO ... LOOP, left only by EXIT.
            t._emit("DO")
        else:
            # DO WHILE <cond> ... LOOP tests at the TOP, matching Python's
            # while. DO ... LOOP WHILE <cond> would be a do-while and
            # always run once.
            t._emit(f"DO WHILE {t._truth(node.test)}")
        t._break_frames.append({"kind": "while", "lines": [], "continues": []})
        for stmt in node.body:
            t.visit(stmt)
        frame = t._break_frames.pop()
        loop_ln = t._emit("LOOP")
        # There is no CONTINUE statement, so jump to the LOOP, which
        # branches back to the top and re-tests.
        for cln in frame["continues"]:
            t.emitter.emit(cln, f"GOTO {loop_ln}")

    def emit_break(self, _node):
        self.t._emit("EXIT")


class Basic65Dialect(_StructuredDialect):
    """
    BASIC 65 (MEGA65) — civilised structured programming.
    BEGIN/BEND, DO WHILE/LOOP, MOD(), SLEEP, UPPER$/LOWER$, INSTR, HEX$.
    """

    has_string_case = True
    has_mod = True

    def emit_modulo(self, left, right):
        return f"MOD({left}, {right})"

    def emit_sleep(self, seconds_expr):
        self.t._emit(f"SLEEP {seconds_expr}")

    def dialect_name(self):
        return "BASIC 65"


class Basic7Dialect(_StructuredDialect):
    """
    BASIC 7.0 (C128) — structured flow, unstructured math.

    Shares BEGIN/BEND IF and DO/LOOP WHILE with BASIC 65.
    No MOD() function — faked with INT() like BASIC 2.0.
    SLEEP takes whole seconds only; anything else is a FOR/NEXT delay loop.
    Has INSTR and HEX$/DEC.
    EXIT works for DO/LOOP break.
    """

    def emit_modulo(self, left, right):
        # BASIC 7.0 has no MOD() — fake it like BASIC 2.0
        return _fake_mod(left, right)

    def emit_sleep(self, seconds_expr):
        # SLEEP n takes whole seconds from 0 to 65535. A fraction or a
        # runtime expression falls back to the delay loop.
        seconds = self.t._static_num_from_text(seconds_expr)
        if seconds is not None and seconds == int(seconds) and 0 <= seconds <= 65535:
            self.t._emit(f"SLEEP {int(seconds)}")
        else:
            self.t.emit_delay_loop(seconds_expr)

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
        "int": (1, 2, None),  # special-cased: VAL() for strings, DEC() base 16
        "float": (1, 1, None),  # special-cased: VAL() for strings
        "str": (1, 1, "STR$({0})"),
        "abs": (1, 1, "ABS({0})"),
        "len": (1, 1, None),  # special-cased: arrays know their own size
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
        # BASIC intrinsics with no Python equivalent.
        "sgn": (1, 1, "SGN({0})"),
        "peek": (1, 1, "PEEK({0})"),
        "pos": (0, 1, None),  # special-cased: POS(0)
        "fre": (0, 1, None),  # special-cased: FRE(0)
        "tab": (1, 1, "TAB({0})"),
        "spc": (1, 1, "SPC({0})"),
        "hex": (1, 1, None),  # special-cased: dialect-gated HEX$()
    }

    COMPARE_OPS: ClassVar[dict] = {
        ast.Eq: "=",
        ast.NotEq: "<>",
        ast.Lt: "<",
        ast.LtE: "<=",
        ast.Gt: ">",
        ast.GtE: ">=",
    }

    #: Python binary operators with a direct BASIC spelling.
    BINARY_OPS: ClassVar[dict] = {
        ast.Add: "+",
        ast.Sub: "-",
        ast.Mult: "*",
        ast.Div: "/",
        ast.Pow: "^",
    }

    #: Modules that may be imported. Only a few of their names map to BASIC.
    SUPPORTED_MODULES = frozenset({"time", "sys", "math", "random", "py2basic_runtime"})

    #: Characters that cannot sit inside a BASIC string literal.
    STRING_CODES: ClassVar[dict] = {'"': "CHR$(34)", "\n": "CHR$(13)"}

    #: name -> (min_args, max_args, BASIC statement template)
    STATEMENT_INTRINSICS: ClassVar[dict] = {
        "poke": (2, 2, "POKE {0}, {1}"),
        "sys_call": (1, 1, "SYS {0}"),
        "wait": (2, 3, None),  # optional XOR operand
        "restore": (0, 0, "RESTORE"),
        "data": (1, None, None),  # variadic
        "stop": (0, 0, "STOP"),
        "basic": (1, 1, None),  # raw escape hatch
    }

    def __init__(self, dialect=None, start=10, step=10):
        self.sym = SymbolTable()
        self.lines = LineAllocator(start, step)
        self.emitter = Emitter()
        self._string_vars = set()
        #: Names of lists whose elements are strings, known before DIM.
        self._string_lists = set()
        #: py name -> (basic_name, size, is_string)
        self._arrays = {}
        #: py name -> (basic_fn_name, param_py_name)
        self._fn_defs = {}
        self._fn_param_scope = None
        self._functions = {}
        self._gosub_backfills = []
        self._toplevel_defs = set()
        self._break_frames = []
        self._forward_targets = set()
        #: local name -> module, for `import x` and `import x as y`.
        self._modules = {name: name for name in self.SUPPORTED_MODULES}
        #: local name -> (module, attribute), for `from x import y [as z]`.
        self._from_imports = {}
        # A dialect emits through the transpiler, so the natural argument
        # is the class; an instance built elsewhere is accepted as well.
        if dialect is None:
            self.dialect = Basic2Dialect(self)
        elif isinstance(dialect, type):
            self.dialect = dialect(self)
        else:
            self.dialect = dialect

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
        if isinstance(node, ast.Subscript):
            base = node.value
            if isinstance(base, ast.Name):
                if base.id in self._arrays:
                    return self._arrays[base.id][2]
                if base.id in self._string_lists:
                    return True
            # A slice of a string is a string.
            return self._is_string_expr(base)
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                if func.id in self.STRING_CALLS:
                    return True
                return func.id in ("hex", "getkey")
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

    def _list_literal(self, value, lineno=0):
        """
        Describe a list-shaped initialiser, or None if it is not one.

        Recognised:
            [a, b, c]      explicit elements
            [x] * n        n copies of a fill value
            [0] * n        the usual "allocate an array" idiom
        """
        if isinstance(value, ast.List):
            return list(value.elts), None
        if isinstance(value, ast.BinOp) and isinstance(value.op, ast.Mult):
            for lst, count in (
                (value.left, value.right),
                (value.right, value.left),
            ):
                if not isinstance(lst, ast.List):
                    continue
                if len(lst.elts) != 1:
                    raise TranspilerError(
                        "Only a one-element list can be repeated, e.g. [0] * 10.",
                        lineno,
                    )
                size = self._static_num(count)
                if size is None:
                    raise TranspilerError(
                        "List size must be a literal number: BASIC arrays are "
                        "sized by DIM when the program starts, e.g. [0] * 10.",
                        lineno,
                    )
                if size < 1 or size != int(size):
                    raise TranspilerError(
                        f"List size must be a positive whole number, not {size}.",
                        lineno,
                    )
                return None, (lst.elts[0], int(size))
        return None

    def _collect_assignments(self, tree):
        """
        Every assignment in the program, split into scalar assignments
        (names, value, lineno) and list declarations (name, sample element).
        """
        pairs = []
        lists = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                names = [t.id for t in node.targets if isinstance(t, ast.Name)]
                if not names:
                    continue
                lineno = getattr(node, "lineno", 0)
                shape = self._list_literal(node.value, lineno)
                if shape is None:
                    pairs.append((names, node.value, lineno))
                    continue
                # A list initialiser types the array, not a scalar.
                elements, fill = shape
                if fill is not None:
                    sample = fill[0]
                else:
                    sample = elements[0] if elements else None
                if sample is not None:
                    lists.extend((name, sample) for name in names)
            elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
                pairs.append(([node.target.id], node.value, getattr(node, "lineno", 0)))
        return pairs, lists

    def _infer_string_vars(self, tree):
        """
        Work out which Python names hold strings.

        Iterates to a fixpoint: `a = "x"` marks a as a string, which in turn
        lets `b = "y" + a` mark b, and so on. A single pass sees only the
        first of those and would emit `B = "y" + A$` — a numeric variable
        holding a string, which is a ?TYPE MISMATCH ERROR on real hardware.

        Lists take part too: `names = ["a"]` makes `names` a string list,
        so `n = names[0]` makes n a string.
        """
        pairs, lists = self._collect_assignments(tree)

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
            for name, sample in lists:
                if name not in self._string_lists and self._is_string_expr(sample):
                    self._string_lists.add(name)
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

    # ── Arrays ─────────────────────────────────────────────────────────────────

    def _array_size(self, node):
        """Element count of the array node refers to, or None."""
        if isinstance(node, ast.Name) and node.id in self._arrays:
            return self._arrays[node.id][1]
        return None

    def _subscript_expr(self, node):
        """Compile array indexing and string slicing."""
        lineno = getattr(node, "lineno", 0)
        base = node.value
        index = node.slice

        # Array element: xs[i] -> A(I)
        if isinstance(base, ast.Name) and base.id in self._arrays:
            if isinstance(index, ast.Slice):
                raise TranspilerError("Slicing a list is not supported.", lineno)
            basic_name, size, _ = self._arrays[base.id]
            const = self._static_num(index)
            if const is not None:
                if const < 0:
                    raise TranspilerError(
                        "Negative list indices are not supported — BASIC "
                        "subscripts start at 0.",
                        lineno,
                    )
                if const >= size:
                    raise TranspilerError(
                        f"Index {int(const)} is out of range for a list of " f"{size}.",
                        lineno,
                    )
            return f"{basic_name}({self._expr(index)})"

        # String slicing: LEFT$ / RIGHT$ / MID$
        if self._is_string_expr(base):
            return self._string_slice(base, index, lineno)

        raise TranspilerError(
            "Subscripting is supported on lists and strings only.", lineno
        )

    def _string_slice(self, base, index, lineno):
        target = self._expr(base)

        if not isinstance(index, ast.Slice):
            # s[i] -> MID$(S$, i + 1, 1); BASIC positions are 1-based.
            const = self._static_num(index)
            if const is not None and const < 0:
                back = int(-const) - 1
                pos = f"LEN({target})" if back == 0 else f"LEN({target}) - {back}"
                return f"MID$({target}, {pos}, 1)"
            return f"MID$({target}, {self._offset(index)}, 1)"

        if index.step is not None:
            raise TranspilerError("Slice steps are not supported.", lineno)

        lower, upper = index.lower, index.upper
        low_const = self._static_num(lower) if lower is not None else 0
        up_const = self._static_num(upper) if upper is not None else None

        # s[:n] -> LEFT$
        if lower is None or low_const == 0:
            if upper is None:
                return target
            if up_const is not None and up_const < 0:
                return f"LEFT$({target}, LEN({target}) - {int(-up_const)})"
            return f"LEFT$({target}, {_atom(self._expr(upper))})"

        # s[-n:] -> RIGHT$
        if upper is None and low_const is not None and low_const < 0:
            return f"RIGHT$({target}, {int(-low_const)})"

        if low_const is not None and low_const < 0:
            raise TranspilerError(
                "A negative slice start is supported only as s[-n:].", lineno
            )

        start = self._offset(lower)
        if upper is None:
            return f"MID$({target}, {start})"
        if up_const is not None and up_const < 0:
            # s[a:-n] -> MID$(S$, a + 1, LEN(S$) - n - a)
            drop = int(-up_const)
            if low_const is not None:
                length = f"LEN({target}) - {drop + int(low_const)}"
            else:
                length = f"LEN({target}) - {drop} - {_atom(self._expr(lower))}"
            return f"MID$({target}, {start}, {length})"
        # Length is upper - lower.
        if low_const is not None and up_const is not None:
            length = str(int(up_const - low_const))
        else:
            length = f"{_atom(self._expr(upper))} - {_atom(self._expr(lower))}"
        return f"MID$({target}, {start}, {length})"

    def _offset(self, node):
        """Python 0-based index as a 1-based BASIC position."""
        const = self._static_num(node)
        if const is not None:
            return str(int(const) + 1)
        return f"{_atom(self._expr(node))} + 1"

    def _basic_var(self, py_name):
        if py_name in self._string_vars:
            return self.sym.get_string(py_name)
        return self.sym.get_numeric(py_name)

    def _escape_commodore_string(self, s: str, lineno: int = 0) -> str:
        """
        Spell a Python string as a BASIC string expression.

        A double quote cannot sit inside a BASIC string literal, and a
        newline would end the program line, so each becomes a CHR$() call
        spliced in with +. Returns a complete BASIC expression, so callers
        must NOT wrap it in additional quotes.

        Examples:
          'hello'          -> '"hello"'
          'say "hi"'       -> '"say " + CHR$(34) + "hi" + CHR$(34)'
          '"quoted"'       -> 'CHR$(34) + "quoted" + CHR$(34)'
          'a\\nb'           -> '"a" + CHR$(13) + "b"'
        """
        for ch in s:
            if ord(ch) < 32 and ch not in self.STRING_CODES:
                raise TranspilerError(
                    f"Control character {ch!r} in a string literal has no "
                    f"BASIC spelling here; concatenate chr(n) instead.",
                    lineno,
                )
        parts = []
        run = []
        for ch in s:
            code = self.STRING_CODES.get(ch)
            if code is None:
                run.append(ch)
                continue
            if run:
                parts.append('"' + "".join(run) + '"')
                run = []
            parts.append(code)
        if run:
            parts.append('"' + "".join(run) + '"')
        return " + ".join(parts) if parts else '""'

    # ── Expression compiler ────────────────────────────────────────────────────

    def _expr(self, node):
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                return "-1" if node.value else "0"
            if isinstance(node.value, (int, float)):
                return str(node.value)
            if isinstance(node.value, str):
                return self._escape_commodore_string(
                    node.value, getattr(node, "lineno", 0)
                )
            raise TranspilerError(
                f"Unsupported constant: {type(node.value)}", getattr(node, "lineno", 0)
            )

        if isinstance(node, ast.Name):
            if node.id == "True":
                return "-1"
            if node.id == "False":
                return "0"
            if self._fn_param_scope is not None and node.id == self._fn_param_scope[0]:
                return self.sym.get_numeric(self._fn_param_scope[1])
            if node.id in self._arrays:
                raise TranspilerError(
                    f"'{node.id}' is a list; BASIC has no whole-array value. "
                    f"Index it, or use len({node.id}).",
                    getattr(node, "lineno", 0),
                )
            return self._basic_var(node.id)

        if isinstance(node, ast.BinOp):
            op = node.op
            lineno = getattr(node, "lineno", 0)
            # String % formatting: "Hello %s" % name  or  "Hi %s %s" % (a, b)
            # Must check before evaluating left/right so we can inspect AST types.
            if (
                isinstance(op, ast.Mod)
                and isinstance(node.left, ast.Constant)
                and isinstance(node.left.value, str)
            ):
                return self._expand_percent_format(node.left.value, node.right, lineno)
            if isinstance(op, ast.Mod):
                # emit_modulo adds whatever parentheses its spelling needs.
                return self.dialect.emit_modulo(
                    self._expr(node.left), self._expr(node.right)
                )
            if isinstance(op, ast.FloorDiv):
                left = self._operand(node.left, _PREC_MUL)
                right = self._operand(node.right, _PREC_MUL, right=True)
                return f"INT({left} / {right})"
            if isinstance(op, (ast.BitAnd, ast.BitOr, ast.BitXor)):
                left = self._expr(node.left)
                right = self._expr(node.right)
                if isinstance(op, ast.BitXor):
                    # XOR is a function in BASIC 7.0 and 65, and absent in 2.0.
                    self._require("has_xor", lineno, "XOR()")
                    return f"XOR({left}, {right})"
                word = "AND" if isinstance(op, ast.BitAnd) else "OR"
                return f"({left}) {word} ({right})"
            symbol = self.BINARY_OPS.get(type(op))
            if symbol is None:
                raise TranspilerError(
                    f"Unsupported operator: {type(op).__name__}", lineno
                )
            prec = _BINOP_PREC[type(op)]
            left = self._operand(node.left, prec)
            right = self._operand(node.right, prec, right=True)
            return f"{left} {symbol} {right}"

        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.Not):
                return f"NOT ({self._truth(node.operand)})"
            if isinstance(node.op, ast.Invert):
                # BASIC's NOT is bitwise: NOT x is -(x + 1), exactly ~x.
                return f"NOT ({self._expr(node.operand)})"
            operand = self._expr(node.operand)
            if isinstance(node.op, ast.USub):
                return f"-{_atom(operand)}"
            if isinstance(node.op, ast.UAdd):
                return operand
            raise TranspilerError(
                f"Unsupported unary op: {type(node.op).__name__}",
                getattr(node, "lineno", 0),
            )

        if isinstance(node, ast.BoolOp):
            op = "AND" if isinstance(node.op, ast.And) else "OR"
            parts = [f"({self._truth(v)})" for v in node.values]
            return f" {op} ".join(parts)

        if isinstance(node, ast.Compare):
            return self._compare_expr(node)

        if isinstance(node, ast.Call):
            return self._call_expr(node)

        if isinstance(node, ast.IfExp):
            raise TranspilerError(
                "Ternary expressions not supported.", getattr(node, "lineno", 0)
            )

        if isinstance(node, ast.Subscript):
            return self._subscript_expr(node)

        if isinstance(node, ast.JoinedStr):
            return self._expand_fstring(node)

        raise TranspilerError(
            f"Unsupported expression: {type(node).__name__}", getattr(node, "lineno", 0)
        )

    # ── Precedence and truth ───────────────────────────────────────────────────

    def _prec(self, node):
        """Precedence of the BASIC text _expr(node) produces; higher binds tighter."""
        if isinstance(node, ast.BinOp):
            op = node.op
            if isinstance(op, ast.Mod):
                is_format = isinstance(node.left, ast.Constant) and isinstance(
                    node.left.value, str
                )
                if is_format or not self.dialect.has_mod:
                    # Concatenation, or the a - INT(a / b) * b fake.
                    return _PREC_ADD
                return _PREC_ATOM
            if isinstance(op, (ast.FloorDiv, ast.BitXor)):
                return _PREC_ATOM  # INT(...) and XOR(...)
            if isinstance(op, ast.BitAnd):
                return _PREC_AND
            if isinstance(op, ast.BitOr):
                return _PREC_OR
            return _BINOP_PREC.get(type(op), _PREC_ATOM)
        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, (ast.Not, ast.Invert)):
                return _PREC_NOT
            if isinstance(node.op, ast.UAdd):
                return self._prec(node.operand)
            return _PREC_NEG
        if isinstance(node, ast.BoolOp):
            return _PREC_AND if isinstance(node.op, ast.And) else _PREC_OR
        if isinstance(node, ast.Compare):
            # A chain becomes (a < b) AND (b < c).
            return _PREC_AND if len(node.ops) > 1 else _PREC_CMP
        if isinstance(node, ast.JoinedStr):
            return _PREC_ADD
        return _PREC_ATOM

    def _operand(self, node, prec, *, right=False):
        """
        Compile node as an operand of an operator of precedence prec,
        parenthesised when BASIC would otherwise bind it differently.

        A right operand of equal precedence is always parenthesised: that
        keeps a - (b - c) and 2 ** (3 ** 2) meaning what they say.
        """
        text = self._expr(node)
        own = self._prec(node)
        if own < prec or (right and own == prec):
            return f"({text})"
        return text

    def _truth(self, node):
        """
        Compile node as a condition that is exactly 0 or -1.

        BASIC's AND, OR and NOT are bitwise, so they are only safe on
        operands that are already proper booleans. A bare value has to be
        compared with zero first: NOT 3 is -4, which is true, so a plain
        `while n:` would exit at once on BASIC 2.0 and `if x and y:` with
        5 and 2 would be false everywhere.
        """
        if isinstance(node, (ast.Compare, ast.BoolOp)):
            return self._expr(node)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return self._expr(node)
        if isinstance(node, ast.Constant) and isinstance(node.value, bool):
            return self._expr(node)
        zero = '""' if self._is_string_expr(node) else "0"
        return f"{self._operand(node, _PREC_CMP)} <> {zero}"

    @staticmethod
    def _is_true_const(node):
        return isinstance(node, ast.Constant) and node.value is True

    # ── Emitter helpers ────────────────────────────────────────────────────────

    def _emit(self, text):
        ln = self.lines.next()
        self.emitter.emit(ln, text)
        return ln

    def _declare_array(self, py_name, value, lineno):
        """Compile a list assignment into DIM plus element initialisers."""
        shape = self._list_literal(value, lineno)
        if shape is None:
            return False
        elements, fill = shape

        if py_name in self._arrays:
            raise TranspilerError(
                f"Array '{py_name}' is already declared. BASIC cannot "
                f"re-DIM an array.",
                lineno,
            )

        if fill is not None:
            fill_node, size = fill
            is_string = self._is_string_expr(fill_node)
            elements = None
        else:
            if not elements:
                raise TranspilerError(
                    "Empty list — BASIC arrays need a fixed size, e.g. " "[0] * 10.",
                    lineno,
                )
            size = len(elements)
            is_string = self._is_string_expr(elements[0])
            for el in elements:
                if self._is_string_expr(el) != is_string:
                    raise TranspilerError(
                        "List mixes strings and numbers; BASIC arrays hold "
                        "one type.",
                        lineno,
                    )

        basic_name = self.sym.get_array(py_name, is_string)
        self._arrays[py_name] = (basic_name, size, is_string)

        # DIM A(n) allocates subscripts 0..n, so a Python list of n elements
        # needs DIM A(n - 1).
        self._emit(f"DIM {basic_name}({size - 1})")

        if elements is not None:
            for index, el in enumerate(elements):
                self._emit(f"{basic_name}({index}) = {self._expr(el)}")
        else:
            zero = "0" if not is_string else '""'
            filled = self._expr(fill_node)
            if filled != zero:
                # BASIC already zero/empty-fills on DIM; only loop if the
                # requested fill differs.
                loop_var = self.sym.get_numeric(f"__fill_{py_name}")
                self._emit(f"FOR {loop_var} = 0 TO {size - 1}")
                self._emit(f"{basic_name}({loop_var}) = {filled}")
                self._emit(f"NEXT {loop_var}")
        return True

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

    @staticmethod
    def _static_num_from_text(expr):
        """The finite number expr spells, or None."""
        try:
            value = float(expr)
        except ValueError:
            return None
        return value if math.isfinite(value) else None

    def emit_delay_loop(self, seconds_expr):
        """
        Fake time.sleep() on dialects with no SLEEP, using the empty
        FOR/NEXT loop the error message used to tell people to write.

        The iteration count is approximate — real timing depends on the
        machine, its ROM and the video standard.
        """
        rate = self.dialect.delay_loop_rate
        seconds = self._static_num_from_text(seconds_expr)
        count = (
            str(int(seconds * rate))
            if seconds is not None
            else f"({seconds_expr}) * {rate}"
        )
        var = self.sym.get_numeric("__delay")
        self._emit(f"FOR {var} = 1 TO {count}")
        self._emit(f"NEXT {var}")

    def _emit_break_goto(self):
        """Reserve a line for a `break` and register it for backfilling."""
        ln = self.lines.next()
        self.emitter.emit(ln, "GOTO 0")  # placeholder, backfilled by the loop
        self._break_frames[-1]["lines"].append(ln)

    def _require(self, flag, lineno, what):
        if not getattr(self.dialect, flag):
            raise TranspilerError(
                f"{what} is not available in {self.dialect.dialect_name()}.",
                lineno,
            )

    def _compare_expr(self, node):
        """
        Compile a comparison, including the chained form.

        `a < b < c` becomes `(a < b) AND (b < c)`. Re-evaluating b is safe
        because BASIC expressions have no side effects.
        """
        lineno = getattr(node, "lineno", 0)
        operands = [node.left, *node.comparators]
        parts = []

        for i, op in enumerate(node.ops):
            left_node, right_node = operands[i], operands[i + 1]

            # `sub in s` -> INSTR(S$, SUB$) > 0
            if isinstance(op, (ast.In, ast.NotIn)):
                self._require("has_instr", lineno, "the `in` operator (INSTR)")
                if not self._is_string_expr(right_node):
                    raise TranspilerError(
                        "`in` is supported for substring tests only.", lineno
                    )
                probe = f"INSTR({self._expr(right_node)}, " f"{self._expr(left_node)})"
                parts.append(f"{probe} {'=' if isinstance(op, ast.NotIn) else '>'} 0")
                continue

            basic_op = self.COMPARE_OPS.get(type(op))
            if not basic_op:
                raise TranspilerError(
                    f"Unsupported comparison: {type(op).__name__}", lineno
                )
            left = self._operand(left_node, _PREC_CMP)
            right = self._operand(right_node, _PREC_CMP, right=True)
            parts.append(f"{left} {basic_op} {right}")

        if len(parts) == 1:
            return parts[0]
        return " AND ".join(f"({p})" for p in parts)

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
                    # Already a string: use it directly; otherwise STR$().
                    if self._is_string_expr(arg_node):
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
                if self._is_string_expr(value.value):
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

    def _random_expr(self, attr, node, lineno):
        """
        random.* -> RND().

        RND(1) returns a float in [0, 1) exactly like random.random().
        """
        args = [self._expr(a) for a in node.args]
        if attr == "random":
            if args:
                raise TranspilerError("random.random() takes no arguments.", lineno)
            return "RND(1)"
        if attr == "seed":
            raise TranspilerError(
                "random.seed() is a statement; call it on its own line.", lineno
            )
        if attr in ("randint", "randrange"):
            if attr == "randint":
                if len(args) != 2:
                    raise TranspilerError("random.randint() takes 2 arguments.", lineno)
                low, high = args
                span = f"{_atom(high)} - {_atom(low)} + 1"
            else:
                if len(args) == 1:
                    low, span = "0", _atom(args[0])
                elif len(args) == 2:
                    low, span = args[0], f"{_atom(args[1])} - {_atom(args[0])}"
                else:
                    raise TranspilerError(
                        "random.randrange() takes 1 or 2 arguments.", lineno
                    )
            base = f"INT(RND(1) * ({span}))"
            return base if low == "0" else f"{base} + {_atom(low)}"
        raise TranspilerError(f"random.{attr}() has no BASIC equivalent.", lineno)

    def _builtin_expr(self, name, node, lineno):
        self._check_call(name, node, lineno)

        # int(s, 16) -> DEC(s$). Handled before the args are compiled so the
        # base can be inspected as a literal.
        if name == "int" and len(node.args) == 2:
            base = self._static_num(node.args[1])
            if base != 16:
                raise TranspilerError(
                    "int(x, base) supports only base 16, which maps to DEC().",
                    lineno,
                )
            self._require("has_hex", lineno, "DEC()")
            return f"DEC({self._expr(node.args[0])})"

        if name == "len":
            size = self._array_size(node.args[0])
            if size is not None:
                return str(size)

        args = [self._expr(a) for a in node.args]

        if name == "log":
            if len(args) == 2:
                return f"(LOG({args[0]}) / LOG({args[1]}))"
            return f"LOG({args[0]})"

        if name in ("int", "float"):
            # A string operand needs VAL(), not INT() — INT("42") is a
            # ?TYPE MISMATCH ERROR on real hardware.
            inner = f"VAL({args[0]})" if self._is_string_expr(node.args[0]) else args[0]
            return f"INT({inner})" if name == "int" else inner

        if name == "len":
            # Arrays were handled above; anything else must be a string.
            if not self._is_string_expr(node.args[0]):
                raise TranspilerError("len() needs a string or a list.", lineno)
            return f"LEN({args[0]})"

        if name in ("pos", "fre"):
            return f"{name.upper()}({args[0] if args else '0'})"

        if name == "hex":
            self._require("has_hex", lineno, "HEX$()")
            return f"HEX$({args[0]})"

        return self.BUILTINS[name][2].format(*args)

    def _call_expr(self, node):
        lineno = getattr(node, "lineno", 0)

        if isinstance(node.func, ast.Name):
            name = node.func.id
            if name in self._fn_defs:
                if len(node.args) != 1 or node.keywords:
                    raise TranspilerError(
                        f"'{name}()' takes exactly 1 argument.", lineno
                    )
                fn_basic = self._fn_defs[name][0]
                return f"{fn_basic}({self._expr(node.args[0])})"
            if name in self._from_imports:
                module, attr = self._from_imports[name]
                return self._module_expr(module, attr, node, lineno)
            if name in self.BUILTINS:
                return self._builtin_expr(name, node, lineno)
            if name == "input":
                raise TranspilerError(
                    "input() can't be used inside an expression. Use "
                    "var = input(...) or var = int(input(...)) on its own line.",
                    lineno,
                )
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
                if obj.id in self._modules:
                    return self._module_expr(self._modules[obj.id], attr, node, lineno)
                if attr in ("find", "index"):
                    self._require("has_instr", lineno, "str.find() (INSTR)")
                    if len(node.args) != 1:
                        raise TranspilerError(
                            f".{attr}() takes exactly 1 argument here.", lineno
                        )
                    # INSTR is 1-based and returns 0 when absent, so -1 maps
                    # it onto Python's 0-based / -1 convention exactly.
                    return (
                        f"INSTR({self._expr(obj)}, " f"{self._expr(node.args[0])}) - 1"
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

    def _module_expr(self, module, attr, node, lineno):
        """A call on one of the supported modules, in expression position."""
        if module == "random":
            return self._random_expr(attr, node, lineno)
        if module in ("math", "py2basic_runtime") and attr in self.BUILTINS:
            # math.sqrt(x) and friends share the table with the bare names.
            return self._builtin_expr(attr, node, lineno)
        if module == "math":
            raise TranspilerError(f"math.{attr}() has no BASIC equivalent.", lineno)
        raise TranspilerError(
            f"{module}.{attr}() has no value here; "
            f"use it as a statement on its own line.",
            lineno,
        )

    def _module_stmt(self, module, attr, node, lineno):
        """A call on one of the supported modules, in statement position."""
        if module == "time" and attr == "sleep":
            if len(node.args) != 1 or node.keywords:
                raise TranspilerError("time.sleep() takes exactly 1 argument.", lineno)
            self.dialect.emit_sleep(self._expr(node.args[0]))
            return
        if module == "sys" and attr == "exit":
            self._emit("END")
            return
        if module == "random" and attr == "seed":
            if len(node.args) != 1 or node.keywords:
                raise TranspilerError("random.seed() takes exactly 1 argument.", lineno)
            # RND(<negative>) reseeds; the result has to land somewhere,
            # so it goes in a scratch variable.
            scratch = self.sym.get_numeric("__rnd_seed")
            seed = self._expr(node.args[0])
            self._emit(f"{scratch} = RND(-ABS({seed}))")
            return
        if module == "py2basic_runtime" and attr in self.STATEMENT_INTRINSICS:
            self._emit_intrinsic(attr, node, lineno)
            return
        if module in ("math", "random", "py2basic_runtime"):
            raise TranspilerError(
                f"{module}.{attr}() returns a value; assign it to a variable.",
                lineno,
            )
        raise TranspilerError(f"{module}.{attr}() is not supported.", lineno)

    def _emit_at(self, line_num, text):
        self.emitter.emit(line_num, text)

    @staticmethod
    def _fn_body_expr(node):
        """
        The single returned expression of a DEF FN candidate, or None.

        BASIC's DEF FN is one expression, so only `return <expr>` — with an
        optional leading docstring — can become one.
        """
        body = [
            s
            for s in node.body
            if not (
                isinstance(s, ast.Expr)
                and isinstance(s.value, ast.Constant)
                and isinstance(s.value.value, str)
            )
        ]
        if len(body) != 1 or not isinstance(body[0], ast.Return):
            return None
        return body[0].value

    def _is_fn_def(self, node):
        """True if node maps onto BASIC's DEF FN rather than GOSUB."""
        a = node.args
        return (
            not node.decorator_list
            and len(a.args) == 1
            and not (a.posonlyargs or a.vararg or a.kwarg or a.kwonlyargs)
            and not a.defaults
            and self._fn_body_expr(node) is not None
        )

    def _emit_fn_def(self, node):
        """def f(x): return <expr>   ->   DEF FNA(X) = <expr>"""
        lineno = node.lineno
        param_py = node.args.args[0].arg
        # Scope the parameter so it cannot collide with a global of the
        # same Python name.
        param_key = f"{node.name}.{param_py}"
        if param_py in self._string_vars:
            raise TranspilerError(
                f"DEF FN parameter '{param_py}' must be numeric — BASIC has "
                f"no string-valued user functions.",
                lineno,
            )
        param_basic = self.sym.get_numeric(param_key)
        fn_basic = self.sym.get_function(node.name)
        self._fn_defs[node.name] = (fn_basic, param_key)

        expr_node = self._fn_body_expr(node)
        if self._is_string_expr(expr_node):
            raise TranspilerError(
                f"Function '{node.name}' returns a string. BASIC's DEF FN "
                f"returns numbers only.",
                lineno,
            )
        self._fn_param_scope = param_py, param_key
        try:
            body = self._expr(expr_node)
        finally:
            self._fn_param_scope = None
        self._emit(f"DEF {fn_basic}({param_basic}) = {body}")

    # ── Statement visitors ─────────────────────────────────────────────────────

    def visit_Module(self, node):
        self._infer_string_vars(node)

        top_defs = [s for s in node.body if isinstance(s, ast.FunctionDef)]

        seen = set()
        for fn in top_defs:
            if fn.name in seen:
                raise TranspilerError(
                    f"Function '{fn.name}' is defined more than once.", fn.lineno
                )
            seen.add(fn.name)
            self._toplevel_defs.add(id(fn))

        # A one-argument single-expression numeric function becomes a BASIC
        # DEF FN; everything else becomes a GOSUB subroutine.
        fn_defs = [s for s in top_defs if self._is_fn_def(s)]
        sub_defs = [s for s in top_defs if s not in fn_defs]
        has_functions = bool(sub_defs)

        # Pre-register subroutines (placeholder=0) so call sites can find
        # them. Only top-level defs qualify; anything nested is rejected in
        # visit_FunctionDef rather than crashing with a KeyError.
        for fn in sub_defs:
            self._functions[fn.name] = 0

        # DEF FN has to run before any FN reference, so it goes first.
        for fn in fn_defs:
            self._emit_fn_def(fn)

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
            for stmt in sub_defs:
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
        if node.decorator_list:
            raise TranspilerError(
                f"Function '{node.name}' has a decorator, which has no "
                f"meaning in BASIC.",
                lineno,
            )
        a = node.args
        if a.args or a.posonlyargs or a.vararg or a.kwonlyargs or a.kwarg:
            raise TranspilerError(
                f"Function '{node.name}' has parameters. Only a single-"
                f"expression numeric function (def f(x): return ...) can "
                f"become a BASIC DEF FN; otherwise use global variables.",
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
        explicit_sep = False
        suppress_newline = False

        for kw in node.keywords:
            if kw.arg == "sep":
                explicit_sep = True
                text = self._const_str_kwarg(kw, lineno)
                if text:
                    sep = f" ; {self._escape_commodore_string(text, lineno)} ; "
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

        parts = []
        for index, arg in enumerate(node.args):
            if (
                index
                and not explicit_sep
                and self._is_string_expr(arg)
                and self._is_string_expr(node.args[index - 1])
            ):
                # PRINT "a" ; "b" runs the strings together where Python
                # puts a space between them. Numbers need no help: BASIC
                # prints them with a space on either side already.
                parts.append('" "')
            parts.append(self._expr(arg))
        stmt = "PRINT" if not parts else f"PRINT {sep.join(parts)}"
        if suppress_newline:
            # A trailing ; leaves the cursor where it is, like end="".
            stmt += ";"
        self._emit(stmt)

    def _emit_intrinsic(self, name, node, lineno):
        low, high, template = self.STATEMENT_INTRINSICS[name]
        if node.keywords:
            raise TranspilerError(
                f"'{name}()' does not accept keyword arguments.", lineno
            )
        count = len(node.args)
        if count < low or (high is not None and count > high):
            want = (
                str(low)
                if low == high
                else f"{low}+" if high is None else f"{low}-{high}"
            )
            raise TranspilerError(
                f"'{name}()' takes {want} argument(s), got {count}.", lineno
            )

        if name == "basic":
            # Raw BASIC passthrough, for dialect commands this transpiler
            # does not model (graphics, sound, disk).
            arg = node.args[0]
            if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
                raise TranspilerError("basic() takes a literal string.", lineno)
            text = arg.value.strip()
            if not text:
                raise TranspilerError("basic() needs a statement.", lineno)
            self._emit(text)
            return

        if name == "data":
            items = []
            for a in node.args:
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    if '"' in a.value or "," in a.value or ":" in a.value:
                        raise TranspilerError(
                            'data() strings cannot contain " , or :', lineno
                        )
                    items.append(f'"{a.value}"')
                else:
                    value = self._static_num(a)
                    if value is None:
                        raise TranspilerError(
                            "data() takes literal numbers and strings only.",
                            lineno,
                        )
                    items.append(str(value))
            self._emit("DATA " + ",".join(items))
            return

        args = [self._expr(a) for a in node.args]
        if name == "wait":
            self._emit("WAIT " + ", ".join(args))
            return
        self._emit(template.format(*args))

    def _compile_call_stmt(self, node):
        lineno = getattr(node, "lineno", 0)

        if isinstance(node.func, ast.Name):
            name = node.func.id

            if name == "print":
                self._emit_print(node, lineno)
                return

            if name == "input":
                raise TranspilerError("Use: var = input('prompt')", lineno)

            if name in self._from_imports:
                module, attr = self._from_imports[name]
                self._module_stmt(module, attr, node, lineno)
                return

            if name == "sleep":
                if len(node.args) != 1:
                    raise TranspilerError("sleep() takes exactly 1 argument.", lineno)
                self.dialect.emit_sleep(self._expr(node.args[0]))
                return

            if name in ("exit", "quit"):
                self._emit("END")
                return

            if name in self.STATEMENT_INTRINSICS:
                self._emit_intrinsic(name, node, lineno)
                return

            if name in self._fn_defs:
                raise TranspilerError(
                    f"'{name}()' is a DEF FN function — use its result, "
                    f"e.g. x = {name}(...).",
                    lineno,
                )

            if name in self._functions:
                ln = self.lines.next()
                self.emitter.emit(ln, "GOSUB 0")
                self._gosub_backfills.append((ln, name))
                return

            raise TranspilerError(f"Unknown function: '{name}()'", lineno)

        if isinstance(node.func, ast.Attribute):
            obj = node.func.value
            attr = node.func.attr
            if isinstance(obj, ast.Name) and obj.id in self._modules:
                self._module_stmt(self._modules[obj.id], attr, node, lineno)
                return
            raise TranspilerError(f"Unsupported method call: .{attr}()", lineno)

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

    @staticmethod
    def _is_call_to(node, names):
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in names
        )

    def _input_call(self, value, lineno):
        """
        Recognise `input(prompt)`, `int(input(prompt))` and
        `float(input(prompt))`, the three ways a program reads a line.

        Returns (True, prompt_node_or_None) for one of those, else
        (False, None). BASIC's INPUT reads a number straight into a
        numeric variable, so the int()/float() wrapper costs nothing.
        """
        call = value
        if (
            self._is_call_to(call, ("int", "float"))
            and len(call.args) == 1
            and not call.keywords
            and self._is_call_to(call.args[0], ("input",))
        ):
            call = call.args[0]
        if not self._is_call_to(call, ("input",)):
            return False, None
        if len(call.args) > 1 or call.keywords:
            raise TranspilerError("input() takes at most one prompt.", lineno)
        return True, (call.args[0] if call.args else None)

    def visit_Assign(self, node):
        lineno = node.lineno
        value = node.value
        if len(node.targets) > 1:
            # a = b = 0: BASIC has no chained assignment, so the value is
            # evaluated once per target. That is only safe for pure values.
            if not all(isinstance(t, ast.Name) for t in node.targets):
                raise TranspilerError(
                    "Chained assignment works for plain variables only.", lineno
                )
            if (
                self._list_literal(value, lineno) is not None
                or self._input_call(value, lineno)[0]
                or self._is_call_to(value, ("getkey", "read"))
            ):
                raise TranspilerError(
                    "Chained assignment cannot take a list, input(), getkey() "
                    "or read(); assign them one at a time.",
                    lineno,
                )
        for target in node.targets:
            self._assign_one(target, value, lineno)

    def _assign_one(self, target, value, lineno):
        # xs[i] = v  ->  A(I) = v
        if isinstance(target, ast.Subscript):
            self._emit(f"{self._subscript_expr(target)} = {self._expr(value)}")
            return

        if not isinstance(target, ast.Name):
            raise TranspilerError("Only simple variable assignment supported", lineno)

        py_name = target.id

        # xs = [0] * 10  ->  DIM A(9)
        if self._declare_array(py_name, value, lineno):
            return

        if py_name in self._arrays:
            raise TranspilerError(
                f"'{py_name}' is a list; assign to its elements instead.", lineno
            )

        # var = getkey()  ->  GET A$     var = read()  ->  READ A
        if self._is_call_to(value, ("getkey", "read")):
            if value.args or value.keywords:
                raise TranspilerError(f"{value.func.id}() takes no arguments.", lineno)
            if value.func.id == "getkey" and py_name not in self._string_vars:
                raise TranspilerError(
                    f"getkey() returns a string; '{py_name}' is numeric. "
                    f"Initialise it with an empty string first.",
                    lineno,
                )
            keyword = "GET" if value.func.id == "getkey" else "READ"
            self._emit(f"{keyword} {self._basic_var(py_name)}")
            return

        # var = input("prompt")  /  n = int(input("prompt"))
        is_input, prompt_node = self._input_call(value, lineno)
        if is_input:
            basic_var = self._basic_var(py_name)
            if prompt_node is not None:
                self._emit(f"PRINT {self._expr(prompt_node)};")
            self._emit(f"INPUT {basic_var}")
            return

        basic_var = self._basic_var(py_name)
        self._emit(f"{basic_var} = {self._expr(value)}")

    def visit_AugAssign(self, node):
        lineno = node.lineno
        if isinstance(node.target, ast.Subscript):
            basic_var = self._subscript_expr(node.target)
        elif isinstance(node.target, ast.Name):
            if node.target.id in self._arrays:
                raise TranspilerError(
                    f"'{node.target.id}' is a list; BASIC cannot operate on a "
                    f"whole array. Update its elements instead.",
                    lineno,
                )
            basic_var = self._basic_var(node.target.id)
        else:
            raise TranspilerError(
                "Augmented assignment only for variables and list elements", lineno
            )
        op = node.op

        if isinstance(op, ast.Mod):
            rhs = self._expr(node.value)
            self._emit(f"{basic_var} = {self.dialect.emit_modulo(basic_var, rhs)}")
        elif isinstance(op, ast.FloorDiv):
            rhs = self._operand(node.value, _PREC_MUL, right=True)
            self._emit(f"{basic_var} = INT({basic_var} / {rhs})")
        elif isinstance(op, ast.BitXor):
            self._require("has_xor", lineno, "XOR()")
            self._emit(f"{basic_var} = XOR({basic_var}, {self._expr(node.value)})")
        elif isinstance(op, (ast.BitAnd, ast.BitOr)):
            word = "AND" if isinstance(op, ast.BitAnd) else "OR"
            self._emit(f"{basic_var} = ({basic_var}) {word} ({self._expr(node.value)})")
        else:
            symbol = self.BINARY_OPS.get(type(op))
            if not symbol:
                raise TranspilerError(
                    f"Unsupported augmented operator: {type(op).__name__}", lineno
                )
            # The right-hand side binds as a unit: x *= y + 1 is x * (y + 1).
            rhs = self._operand(node.value, _BINOP_PREC[type(op)], right=True)
            self._emit(f"{basic_var} = {basic_var} {symbol} {rhs}")

    def visit_If(self, node):
        self.dialect.emit_if(node)

    def visit_While(self, node):
        if node.orelse:
            raise TranspilerError("while/else not supported", node.lineno)
        self.dialect.emit_while(node)

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
            # The bound may still be constant after compiling — len() of an
            # array folds to a literal, for instance.
            compiled = self._expr(stop_node)
            stop_val = self._static_num_from_text(compiled)
            if stop_val is None:
                sign = "-" if delta < 0 else "+"
                stop_expr = f"{_atom(compiled)} {sign} 1"
            else:
                stop_val = int(stop_val) + delta
                stop_expr = str(stop_val)
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

        self._break_frames.append({"kind": "for", "lines": [], "continues": []})
        for stmt in node.body:
            self.visit(stmt)
        frame = self._break_frames.pop()

        next_ln = self._emit(f"NEXT {basic_var}")
        # continue skips the rest of the body and runs the NEXT.
        for cln in frame["continues"]:
            self._emit_at(cln, f"GOTO {next_ln}")

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
        if not self._break_frames:
            raise TranspilerError("continue outside loop", node.lineno)
        ln = self.lines.next()
        self.emitter.emit(ln, "GOTO 0")  # placeholder, backfilled by the loop
        self._break_frames[-1]["continues"].append(ln)

    def visit_Pass(self, _node):
        self._emit("REM")

    def visit_Import(self, node):
        for alias in node.names:
            if alias.name not in self.SUPPORTED_MODULES:
                raise TranspilerError(
                    f"import {alias.name} not supported.", node.lineno
                )
            self._modules[alias.asname or alias.name] = alias.name

    def visit_ImportFrom(self, node):
        if node.module not in self.SUPPORTED_MODULES:
            raise TranspilerError(
                f"from {node.module} import ... not supported.", node.lineno
            )
        for alias in node.names:
            if alias.name == "*":
                raise TranspilerError(
                    f"from {node.module} import * is not supported; "
                    f"name what you use.",
                    node.lineno,
                )
            if node.module == "py2basic_runtime":
                # Intrinsics are recognised by their own names.
                if alias.asname:
                    raise TranspilerError(
                        f"'{alias.name}' from py2basic_runtime cannot be renamed.",
                        node.lineno,
                    )
                continue
            self._from_imports[alias.asname or alias.name] = (node.module, alias.name)

    def visit_Global(self, node):
        raise TranspilerError(
            "global not needed — all BASIC variables are global.", node.lineno
        )

    def visit_Nonlocal(self, node):
        raise TranspilerError("nonlocal not supported.", node.lineno)

    def visit_Delete(self, node):
        raise TranspilerError("del not supported.", node.lineno)

    def visit_Assert(self, node):
        cond = self._truth(node.test)
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
        t = Transpiler(dialect_cls, start=args.start, step=args.step)
        print(f"-- Dialect: {t.dialect.dialect_name()}", file=sys.stderr)
        result = t.transpile(source)
    except TranspilerError as e:
        print(f"Transpiler error: {e}", file=sys.stderr)
        sys.exit(1)

    limit = t.dialect.max_line_length
    long_lines = [ln for ln in result.splitlines() if len(ln) > limit]
    if long_lines:
        first = long_lines[0].split(" ", 1)[0]
        print(
            f"Warning: {len(long_lines)} line(s) exceed {limit} characters "
            f"(first: line {first}). The {t.dialect.dialect_name()} screen "
            f"editor cannot take them; load the program from a file instead.",
            file=sys.stderr,
        )

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
