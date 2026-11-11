# The report

`main.tex` is the write-up of the audit, a standalone technical report. It is
not submitted to any venue. It uses the TMLR style file (`tmlr.sty`,
`tmlr.bst`, from JmlrOrg/tmlr-style-file) in its `preprint` form, which prints
the author and no review header. Build with `make paper` from the repo root,
or:

    cd paper && latexmk -pdf main.tex

`main.pdf` is tracked, so reading the report needs no TeX install.

## Numbers

Every number in the report is recomputed from committed records by
`make numbers`, which writes [NUMBERS.md](NUMBERS.md). It reads no API key and
no response cache. If a value there disagrees with `main.tex`, the report is
stale. This check caught the stale negative-control denominator (0/37, not
0/40) after the degeneracy rule changed.

Tables and figures come from `python3 scripts/paper_assets.py`, which writes
`generated/` and `figures/`. `--check` derives everything and writes nothing.

## What the report does not cover

The data were collected 17 to 23 September 2026 on one provider. Provider
behaviour moves, so a rerun of the probe matrix today would be a new dated
snapshot, not a correction of this one. A second provider would test whether
the findings are specific to Fireworks. The adapters for six more providers
exist (`samplerconfound/adapters.py`) and have not been run.
