# example.py
# Demonstrates the supported Python subset for py2basic65
# Run: python3 transpiler.py example.py

# import time  -- not available in BASIC 2.0, use FOR loop delay

# ── Simple variables and math ──────────────────────────────────────────────────
pi_approx = 355 / 113
radius = 5
area = pi_approx * radius * radius

print('CIRCLE "AREA" CALCULATOR')
print("RADIUS =")
print(radius)
print("AREA =")
print(area)

# ── String handling ────────────────────────────────────────────────────────────
greeting = "HELLO FROM The Python Transpiler."
print(greeting)
print(len(greeting))

# ── For loop ──────────────────────────────────────────────────────────────────
print("COUNTING:")
for i in range(1, 6):
    print(i)

# ── While loop ────────────────────────────────────────────────────────────────
n = 10
total = 0
while n > 0:
    total += n
    n -= 1
print("SUM 1-10 =")
print(total)

# ── if/elif/else ──────────────────────────────────────────────────────────────
score = 75
if score >= 90:
    print("GRADE A")
elif score >= 80:
    print("GRADE B")
elif score >= 70:
    print("GRADE C")
else:
    print("GRADE F")


# ── Subroutine (no params, no return) ─────────────────────────────────────────
def banner():
    print("==================")
    print("   PY2BASIC DEMO  ")
    print("==================")


banner()

# ── Pause ─────────────────────────────────────────────────────────────────────
for dl in range(5000):
    pass
print("DONE")
