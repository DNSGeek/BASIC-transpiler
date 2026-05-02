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
import sys
import textwrap
from abc import ABC, abstractmethod

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
        self._current = start
        self._step = step

    def next(self):
        n = self._current
        self._current += self._step
        return n

    def peek(self):
        return self._current


# ── Emitter ────────────────────────────────────────────────────────────────────


class Emitter:
    def __init__(self):
        self._lines = []

    def emit(self, line_num, text):
        for i, (ln, _) in enumerate(self._lines):
            if ln == line_num:
                self._lines[i] = (line_num, text)
                return
        self._lines.append((line_num, text))

    def output(self):
        self._lines.sort(key=lambda x: x[0])
        return "\n".join(f"{n} {t}" for n, t in self._lines)


# ── Dialect code generators ────────────────────────────────────────────────────


class DialectEmitter(ABC):
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
            t.emitter.emit(cond_ln, f"IF NOT ({cond}) THEN GOTO {past_body}")
        else:
            # Need a GOTO to skip the else after body executes
            skip_else_ln = t.lines.next()

            if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
                # elif chain
                else_start = t.lines.peek()
                t.emitter.emit(cond_ln, f"IF NOT ({cond}) THEN GOTO {else_start}")
                self._emit_if_recursive(node.orelse[0])
            else:
                # else block
                else_start = t.lines.peek()
                t.emitter.emit(cond_ln, f"IF NOT ({cond}) THEN GOTO {else_start}")
                for stmt in node.orelse:
                    t.visit(stmt)

            # Backfill the skip-else jump
            end_ln = t.lines.peek()
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
        t._break_targets.append([])

        # Emit body
        for stmt in node.body:
            t.visit(stmt)

        # GOTO back to top
        t.emitter.emit(t.lines.next(), f"GOTO {top_ln}")

        # End of loop
        end_ln = t.lines.peek()
        t.emitter.emit(cond_ln, f"IF NOT ({cond}) THEN GOTO {end_ln}")

        # Backfill breaks
        break_lines = t._break_targets.pop()
        for bln in break_lines:
            t.emitter.emit(bln, f"GOTO {end_ln}")

    def emit_break(self, node):
        t = self.t
        if not t._break_targets:
            raise TranspilerError("break outside loop", node.lineno)
        ln = t.lines.next()
        t.emitter.emit(ln, "GOTO 0")  # placeholder
        t._break_targets[-1].append(ln)

    def emit_modulo(self, left, right):
        return f"({left}) - INT(({left}) / ({right})) * ({right})"

    def emit_sleep(self, seconds_expr):
        raise TranspilerError(
            "time.sleep() is not available in BASIC 2.0. "
            "Use a FOR loop delay instead: FOR DL=1 TO 5000:NEXT DL"
        )

    def dialect_name(self):
        return "BASIC 2.0"


class Basic65Dialect(DialectEmitter):
    """
    BASIC 65 (MEGA65) — civilised structured programming.
    BEGIN/BEND, DO/LOOP WHILE, MOD(), SLEEP.
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
        t._emit("DO")
        t._break_targets.append([])
        for stmt in node.body:
            t.visit(stmt)
        t._break_targets.pop()
        t._emit(f"LOOP WHILE {cond}")

    def emit_break(self, node):
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
        t._emit("DO")
        t._break_targets.append([])
        for stmt in node.body:
            t.visit(stmt)
        t._break_targets.pop()
        t._emit(f"LOOP WHILE {cond}")

    def emit_break(self, node):
        self.t._emit("EXIT")

    def emit_modulo(self, left, right):
        # BASIC 7.0 has no MOD() — fake it like BASIC 2.0
        return f"({left}) - INT(({left}) / ({right})) * ({right})"

    def emit_sleep(self, seconds_expr):
        raise TranspilerError(
            "time.sleep() is not available in BASIC 7.0. "
            "Use a FOR loop delay instead: FOR DL=1 TO 5000:NEXT DL"
        )

    def dialect_name(self):
        return "BASIC 7.0"


# ── Main transpiler ────────────────────────────────────────────────────────────


class Transpiler(ast.NodeVisitor):
    def __init__(self, dialect=None):
        self.sym = SymbolTable()
        self.lines = LineAllocator()
        self.emitter = Emitter()
        self._string_vars = set()
        self._functions = {}
        self._gosub_backfills = []
        self._break_targets = []
        self._in_function = False
        self.dialect = dialect or Basic2Dialect(self)

    # ── Type inference ─────────────────────────────────────────────────────────

    def _infer_string_vars(self, tree):
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                if isinstance(node.value, ast.Constant) and isinstance(
                    node.value.value, str
                ):
                    for t in node.targets:
                        if isinstance(t, ast.Name):
                            self._string_vars.add(t.id)
                if isinstance(node.value, ast.Call):
                    func = node.value.func
                    if isinstance(func, ast.Name) and func.id in {
                        "str",
                        "input",
                        "chr",
                    }:
                        for t in node.targets:
                            if isinstance(t, ast.Name):
                                self._string_vars.add(t.id)

    def _basic_var(self, py_name, node=None):
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
            return self._basic_var(node.id, node)

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
                return f"-({operand})"
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

        Supported: %s, %d, %i, %f, %g  (all become STR$() for numeric, direct for strings)
        Not supported: width/precision specifiers like %-10s, %05d, etc.
        """
        import re

        # Collect the argument nodes into a list
        if isinstance(args_node, ast.Tuple):
            arg_nodes = list(args_node.elts)
        else:
            arg_nodes = [args_node]

        # Split format string on % specifiers
        # Matches %s, %d, %i, %f, %g, %r (basic ones without width/precision)
        parts = re.split(r"(%[sdifrg])", fmt)

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

    def _call_expr(self, node):
        lineno = getattr(node, "lineno", 0)

        def arg(i):
            return self._expr(node.args[i])

        if isinstance(node.func, ast.Name):
            name = node.func.id
            if name == "int":
                return f"INT({arg(0)})"
            if name == "float":
                return arg(0)
            if name == "str":
                return f"STR$({arg(0)})"
            if name == "abs":
                return f"ABS({arg(0)})"
            if name == "len":
                return f"LEN({arg(0)})"
            if name == "chr":
                return f"CHR$({arg(0)})"
            if name == "ord":
                return f"ASC({arg(0)})"
            if name == "round":
                return f"INT({arg(0)} + 0.5)"
            if name == "sqrt":
                return f"SQR({arg(0)})"
            if name == "sin":
                return f"SIN({arg(0)})"
            if name == "cos":
                return f"COS({arg(0)})"
            if name == "tan":
                return f"TAN({arg(0)})"
            if name == "log":
                if len(node.args) == 2:
                    return f"(LOG({arg(0)}) / LOG({arg(1)}))"
                return f"LOG({arg(0)})"
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
                if attr == "upper":
                    return f"UPPER$({self._expr(obj)})"
                if attr == "lower":
                    return f"LOWER$({self._expr(obj)})"
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

        func_names = [s.name for s in node.body if isinstance(s, ast.FunctionDef)]
        has_functions = bool(func_names)

        # Pre-register functions (placeholder=0) so call sites can find them
        for name in func_names:
            self._functions[name] = 0

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

    def visit_FunctionDef(self, node):
        lineno = node.lineno
        if node.args.args or node.args.vararg or node.args.kwarg:
            raise TranspilerError(
                f"Function '{node.name}' has parameters. "
                f"BASIC subroutines cannot take parameters. "
                f"Use global variables instead.",
                lineno,
            )

        func_ln = self._functions[node.name]
        self._emit_at(func_ln, f"REM -- {node.name}")

        self._in_function = True
        for stmt in node.body:
            self.visit(stmt)
        self._in_function = False

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

    def _compile_call_stmt(self, node):
        lineno = getattr(node, "lineno", 0)

        if isinstance(node.func, ast.Name):
            name = node.func.id

            if name == "print":
                parts = [self._expr(a) for a in node.args]
                self._emit("PRINT" if not parts else f"PRINT {' ; '.join(parts)}")
                return

            if name == "input":
                raise TranspilerError("Use: var = input('prompt')", lineno)

            if name in {"sleep"}:
                if node.args:
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
                    if node.args:
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
            basic_var = self._basic_var(py_name, node)
            if prompt:
                self._emit(f"PRINT {prompt};")
            self._emit(f"INPUT {basic_var}")
            return

        basic_var = self._basic_var(py_name, node)
        self._emit(f"{basic_var} = {self._expr(value)}")

    def visit_AugAssign(self, node):
        if not isinstance(node.target, ast.Name):
            raise TranspilerError(
                "Augmented assignment only for simple variables", node.lineno
            )
        py_name = node.target.id
        basic_var = self._basic_var(py_name, node)
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
        if len(args) == 1:
            start_expr = "0"
            sn = args[0]
            stop_expr = (
                str(sn.value - 1)
                if isinstance(sn, ast.Constant)
                else f"({self._expr(sn)}) - 1"
            )
            step_expr = None
        elif len(args) == 2:
            start_expr = self._expr(args[0])
            sn = args[1]
            stop_expr = (
                str(sn.value - 1)
                if isinstance(sn, ast.Constant)
                else f"({self._expr(sn)}) - 1"
            )
            step_expr = None
        elif len(args) == 3:
            start_expr = self._expr(args[0])
            sn, stepn = args[1], args[2]
            sv = stepn.value if isinstance(stepn, ast.Constant) else None
            if isinstance(sn, ast.Constant):
                stop_expr = str(sn.value + 1) if (sv and sv < 0) else str(sn.value - 1)
            else:
                stop_expr = f"({self._expr(sn)}) - 1"
            step_expr = self._expr(stepn)
        else:
            raise TranspilerError("range() takes 1-3 arguments", lineno)

        header = f"FOR {basic_var} = {start_expr} TO {stop_expr}"
        if step_expr:
            header += f" STEP {step_expr}"
        self._emit(header)

        for stmt in node.body:
            self.visit(stmt)

        self._emit(f"NEXT {basic_var}")

    def visit_Break(self, node):
        if not self._break_targets:
            raise TranspilerError("break outside loop", node.lineno)
        self.dialect.emit_break(node)

    def visit_Continue(self, node):
        raise TranspilerError(
            "continue not supported. Restructure using an if/else guard.", node.lineno
        )

    def visit_Pass(self, node):
        self._emit("REM")

    def visit_Import(self, node):
        for alias in node.names:
            if alias.name not in {"time", "sys", "math"}:
                raise TranspilerError(
                    f"import {alias.name} not supported.", node.lineno
                )

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
            raise TranspilerError(f"Python syntax error: {e}")
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
          --basic7   BASIC 7.0  C128  [not yet implemented]

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

    try:
        with open(args.input) as f:
            source = f.read()
    except FileNotFoundError:
        print(f"Error: file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    t = Transpiler()
    t.lines = LineAllocator(args.start, args.step)
    if args.basic65:
        t.dialect = Basic65Dialect(t)
    elif args.basic7:
        t.dialect = Basic7Dialect(t)
    else:
        t.dialect = Basic2Dialect(t)

    print(f"-- Dialect: {t.dialect.dialect_name()}", file=sys.stderr)

    try:
        result = t.transpile(source)
    except TranspilerError as e:
        print(f"Transpiler error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.output:
        with open(args.output, "w") as f:
            f.write(result + "\n")
        print(f"Written to {args.output}", file=sys.stderr)
    else:
        print(result)

    if args.vars:
        print("\n" + t.sym.dump(), file=sys.stderr)


if __name__ == "__main__":
    main()
