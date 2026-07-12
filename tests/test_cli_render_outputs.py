import json

from doku.detectors import EntrypointCandidate
from doku.state import StateLayout, render_outputs

CANDIDATE = EntrypointCandidate(
    type="REST",
    file="OrderController.java",
    line=20,
    class_name="OrderController",
    method_name="createOrder",
    meta={},
)

VALID_DOC = {
    "title": "Create order",
    "type": "REST",
    "location": "OrderController.java:20",
    "input_model": "...",
    "output_model": "...",
    "flow_mermaid": "flowchart TD\n  A --> B",
    "dependencies": [],
}


def _setup(tmp_path):
    layout = StateLayout(tmp_path)
    layout.ensure_dirs()
    return layout


def test_writes_docs_for_valid_results(tmp_path):
    layout = _setup(tmp_path)
    (layout.results_dir / f"{CANDIDATE.slug}.json").write_text(json.dumps(VALID_DOC))

    render_outputs(layout, [CANDIDATE])

    assert (tmp_path / "entrypoints" / f"{CANDIDATE.slug}.md").exists()
    assert not (tmp_path / "_errors.md").exists()


def test_missing_result_is_reported_as_an_error(tmp_path):
    layout = _setup(tmp_path)
    # no result file written for CANDIDATE

    errors = render_outputs(layout, [CANDIDATE])

    assert errors and errors[0]["slug"] == CANDIDATE.slug

    assert (tmp_path / "_errors.md").exists()
    assert CANDIDATE.slug in (tmp_path / "_errors.md").read_text()


def test_stale_errors_file_is_cleared_on_a_later_successful_run(tmp_path):
    layout = _setup(tmp_path)
    (tmp_path / "_errors.md").write_text("# stale error from a previous run\n")
    (layout.results_dir / f"{CANDIDATE.slug}.json").write_text(json.dumps(VALID_DOC))

    render_outputs(layout, [CANDIDATE])

    assert not (tmp_path / "_errors.md").exists()
