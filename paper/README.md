# Manuscript

`main.tex` builds against the official TMLR style file (`tmlr.sty`, `tmlr.bst`,
fetched from JmlrOrg/tmlr-style-file). Build with `make paper` from the repo
root, or:

    cd paper && latexmk -pdf main.tex

Currently 7 pages plus references. `\usepackage{tmlr}` is the anonymous
submission form; switch to `\usepackage[accepted]{tmlr}` on acceptance.

## Numbers

Every number in the manuscript is recomputed from committed records by
`make numbers`, which writes [NUMBERS.md](NUMBERS.md). It reads no API key and
no response cache. If a value there disagrees with `main.tex`, the paper is
stale — this is how the stale negative-control denominator (0/37, not 0/40)
was caught after the degeneracy rule changed.

## Before submitting

- `python3 scripts/anonymize.py --out ../sampler-confound-anon` and push that
  copy to an anonymous remote; the public repo de-anonymises a double-blind
  submission.
- Re-run the probe matrix and report the drift. Provider behaviour moves, and
  a table collected weeks before submission should say so.
- Decide on the cross-provider extension (see the repo README's Status): the
  single-provider limitation is stated in the paper's Limitations, but a
  reviewer may reasonably ask for a second stack.
