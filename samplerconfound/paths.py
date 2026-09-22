"""Path helpers.

`Path.relative_to` raises when its argument is already relative, which is how a
`--out runs/foo.json` on the command line crashed three separate scripts at the
very end of a run that had already done all its work and written its output. The
loss is not the crash, it is the exit code: a wrapper checking it would treat a
completed pilot as a failure.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def resolve_out(p: Path | str) -> Path:
    """Interpret a path as absolute, or relative to the repository root."""
    p = Path(p)
    return p if p.is_absolute() else ROOT / p


def show(p: Path | str) -> str:
    """Display form: repo-relative when inside the repo, absolute otherwise."""
    p = resolve_out(p)
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def iso(t: float | None) -> str:
    """Unix time -> 'YYYY-MM-DDTHH:MMZ'. Every probe stamps its cells with this."""
    import datetime as _dt
    if t is None:
        return ""
    return _dt.datetime.fromtimestamp(t, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def window(times) -> dict:
    """{'from', 'to'} over a collection of unix times, ISO, ignoring None."""
    ts = [t for t in times if t is not None]
    return {"from": iso(min(ts)), "to": iso(max(ts))} if ts else {"from": "", "to": ""}
