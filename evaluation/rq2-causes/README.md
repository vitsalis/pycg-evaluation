# RQ2: causes of the false positives and the false negatives

**What it measures.** Why each false positive and each false negative of
PyCG on the 30 packages of the macro-benchmark is there. Every such edge
has one label: the mechanism that explains it. Each label is in one of
five groups: a stated limit (a kind of edge that the design of PyCG leaves
out on purpose), a scorer artefact (two names of one function), a
ground-truth doubt, a rule that was measured and is not built, or a gap (a
mechanism that the design does not state as a limit: PyCG lacks a rule, or
has a wrong one).

**Command** (`C="docker compose -f evaluation/compose.yaml"`):

```sh
$C run --rm rq2   # reads the committed label data; runs no analysis
```

**Inputs.** `labels.json`: per batch, one row per edge (package, caller,
callee, `fn` or `fp`, `inside` or `outside`, label, note), and the engine
that the labels are of (its commit, and the state of the ground truths).
`limits.json`: the group of each label; for a
stated limit its name, its family and, where the design names it, the
section of PyCG's `docs/design.md`.

**Outputs** (in `../results/`): `rq2-causes.md` and `.json`. Counts only:
the edges stay in `labels.json`.

**How to read the table.** The headline is the share of the edges under
stated limits. "The groups" has one row per group. "Stated limits" has one
row per limit, the largest first, with its family and the number of
packages where it occurs. "Per batch" has the four counts of each batch
(inside and outside, FN and FP); they are the counts of PyCG in
`../results/rq1-accuracy.md`, and a test checks that.

**Known limits.** The labels are of PyCG `c5c6a89` and of the ground
truths after the audit of 2026-11-16. After a change of the engine or of a ground truth that
changes the counts, a test fails until the changed edges are labelled. A
label names the first mechanism that explains an edge; an edge can have a
second cause. Analysts gave the labels: they checked the large groups with
counterfactual runs and read the rest from the source and the ledgers.

Reference: [`../README.md`](../README.md), section "RQ2: causes".
