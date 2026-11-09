"""Generate deterministic sample data: a 300-row orders table and a drifted copy."""
import csv
import random
from pathlib import Path

out = Path(__file__).resolve().parent.parent / "data"
rng = random.Random(42)
cols = ["order_id", "customer", "amount", "status", "updated_at"]
rows = []
for i in range(1, 301):
    rows.append([i, f"cust_{rng.randint(1, 40):02d}", f"{rng.uniform(5, 500):.2f}",
                 rng.choice(["new", "paid", "shipped"]), "2026-11-01T00:00:00"])

tgt = [r[:] for r in rows]
tgt[9][3] = "refunded"            # order 10: status drift
tgt[120][2] = "999.99"            # order 121: amount drift
tgt[121][1] = " " + tgt[121][1]   # order 122: whitespace only (ignored by default)
tgt[200][2] = rows[200][2] + "0"  # order 201: 12.30 vs 12.300 (numeric-equal)
for r in tgt:
    r[4] = "2026-11-02T09:30:00"  # every row touched by ETL; use --ignore updated_at
del tgt[250:253]                  # orders 251-253 missing in target
tgt.append([999, "cust_01", "10.00", "new", "2026-11-02T09:30:00"])  # extra row

for name, data in (("source.csv", rows), ("target.csv", tgt)):
    with open(out / name, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        w.writerows(data)
