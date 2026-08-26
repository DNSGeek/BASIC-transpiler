# example_keywords.py
# The keywords added beyond the original subset.
# Run:       python3 example_keywords.py
# Transpile: python3 transpiler.py example_keywords.py --basic65
import random

from py2basic_runtime import data, peek, poke, read, sgn


# ── DEF FN: one argument, one expression ──────────────────────────────────────
def double(n):
    return n * 2


# ── String slicing -> LEFT$ / RIGHT$ / MID$ ───────────────────────────────────
title = "COMMODORE BASIC"
print(title[:9])
print(title[-5:])
print(title[10:15])
print(title[0])

# ── VAL: a numeric string becomes a number ────────────────────────────────────
digits = "42"
answer = int(digits)
print(answer)

# ── Lists become DIM'd arrays ─────────────────────────────────────────────────
squares = [0] * 8
for i in range(len(squares)):
    squares[i] = double(i)

total = 0
for i in range(len(squares)):
    total += squares[i]
print(total)

# ── continue and break ────────────────────────────────────────────────────────
n = 0
while n < 10:
    n += 1
    if n == 3:
        continue
    if n == 7:
        break
    print(n)

# ── Chained comparison ────────────────────────────────────────────────────────
score = 75
if 70 <= score < 80:
    print("GRADE C")

# ── Random numbers -> RND ─────────────────────────────────────────────────────
random.seed(1)
roll = random.randint(1, 6)
print(roll > 0)

# ── Hardware intrinsics ───────────────────────────────────────────────────────
poke(53280, 0)
border = peek(53280)
print(border)
print(sgn(-7))

# ── DATA / READ ───────────────────────────────────────────────────────────────
data(10, 20, 30)
first = read()
second = read()
print(first + second)
