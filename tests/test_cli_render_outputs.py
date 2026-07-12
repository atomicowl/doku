import json

from doku.state import StateLayout, read_manifest, render_outputs

SLUG = "rest-OrderController-createOrder"

MANIFEST_ENTRY = {
    "kind": "REST",
    "name": "OrderController.createOrder",
    "file": "OrderController.java",
    "line": 20,
    "meta": {},
    "slug": SLUG,
}

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
    (layout.results_dir / f"{SLUG}.json").write_text(json.dumps(VALID_DOC))

    render_outputs(layout, [SLUG])

    assert (tmp_path / "entrypoints" / f"{SLUG}.md").exists()
    assert not (tmp_path / "_errors.md").exists()


def test_missing_result_is_reported_as_an_error(tmp_path):
    layout = _setup(tmp_path)
    # no result file written for the slug

    errors = render_outputs(layout, [SLUG])

    assert errors and errors[0]["slug"] == SLUG

    assert (tmp_path / "_errors.md").exists()
    assert SLUG in (tmp_path / "_errors.md").read_text()


def test_stale_errors_file_is_cleared_on_a_later_successful_run(tmp_path):
    layout = _setup(tmp_path)
    (tmp_path / "_errors.md").write_text("# stale error from a previous run\n")
    (layout.results_dir / f"{SLUG}.json").write_text(json.dumps(VALID_DOC))

    render_outputs(layout, [SLUG])

    assert not (tmp_path / "_errors.md").exists()


def test_read_manifest_returns_orchestrator_written_candidates(tmp_path):
    layout = _setup(tmp_path)
    layout.entrypoints_json.write_text(json.dumps([MANIFEST_ENTRY]))

    entries = read_manifest(layout)

    assert entries == [MANIFEST_ENTRY]


def test_read_manifest_missing_file_yields_empty(tmp_path):
    layout = _setup(tmp_path)
    assert read_manifest(layout) == []


def test_read_manifest_tolerates_garbage(tmp_path):
    layout = _setup(tmp_path)
    layout.entrypoints_json.write_text("not json at all")
    assert read_manifest(layout) == []

    layout.entrypoints_json.write_text(json.dumps({"oops": "an object, not a list"}))
    assert read_manifest(layout) == []

    layout.entrypoints_json.write_text(json.dumps(["a string entry", MANIFEST_ENTRY]))
    assert read_manifest(layout) == [MANIFEST_ENTRY]


def test_read_manifest_derives_missing_slug(tmp_path):
    layout = _setup(tmp_path)
    entry = {k: v for k, v in MANIFEST_ENTRY.items() if k != "slug"}
    layout.entrypoints_json.write_text(json.dumps([entry]))

    (parsed,) = read_manifest(layout)

    assert parsed["slug"] == "rest-ordercontroller-createorder"  # kind-name, dots sanitized
