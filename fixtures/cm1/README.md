# cm1 — a human-built answer matrix

CM-1 is a NASA scientific instrument, published through the CoEST traceability
community. **22 requirements, 53 design elements, 45 hand-traced links out of
1,166 candidate pairs.**

```sh
./fetch.py
../../freeze.py --project .
./score-retrieval.py
```

## Why it is here

Every other precision and recall figure in this project is **end to end**, which
means a retrieval miss and a judgement error are indistinguishable. They are not
the same problem: one is fixed by changing how passages are selected, the other
by changing what the model is asked.

CM-1 isolates retrieval. Its requirements are already atomic `shall` statements
with stable IDs, so extraction cannot be blamed; each design element is its own
heading, so a chunk is exactly one artefact. What is left is the narrow,
answerable question: **given this requirement, does the retriever surface the
passages a human tracer linked to it?**

## First measurement

| k | recall@k (embed) | recall@k (terms) |
|---:|---:|---:|
| 4 | 73.3% | 60.0% |
| 6 | 84.4% | 66.7% |
| **12** | **95.6%** | 86.7% |
| 24 | 100.0% | 100.0% |

Two conclusions, both of which had been assumptions:

**`passages=12` is defensible after all.** It was chosen on the synthetic fixture
and flagged in DESIGN.md 4a as the highest overfitting risk — selected where
retrieval returned 28.6% of the document. A third-party, human-labelled corpus
independently puts recall at 95.6% there against 73.3% at k=4. Different corpus,
different measurement, same answer.

**Embedding retrieval genuinely beats term overlap**, by 9–18 points at every k
below saturation. On the synthetic fixture the two scored identically, because
that corpus is too small to discriminate.

They also explain the shape seen elsewhere: recall keeps rising to k=24 while
end-to-end accuracy falls there. More retrieval buys evidence and costs
judgement. The optimum is a trade-off, not a peak.

## Limits

- A trace link means *"bears on"*, not *"discharges"*. This measures retrieval
  cleanly and coverage judgement only indirectly.
- 19 requirements with links is a small sample; treat differences under ~10
  points as noise.
- 3 requirements have no gold link (`SRS5.12.4.1`, `SRS5.12.4.2`, `SRS5.13.3.3`).
  In coverage terms those are the only ones that should read unmet.

Data courtesy of NASA, collated by Jane Huffman Hayes. Cite Hayes, Dekhtyar &
Sundaram, *IEEE TSE* 32(1):4-19 (2006) — see `fetch.py`.
