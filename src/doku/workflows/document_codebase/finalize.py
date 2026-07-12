"""Finalization adapter owned by the bundled documentation workflow."""

from doku.state import StateLayout, read_manifest, render_outputs


def finalize(out, result):
    layout = StateLayout(out)
    candidates = read_manifest(layout)
    errors = render_outputs(layout, [candidate["slug"] for candidate in candidates])
    return {
        "items": len(candidates),
        "errors": len(errors),
        "message": f"Discovered {len(candidates)} item(s); {len(errors)} failed.",
    }
