import json

import pytest

from doku.state import parse_entrypoint_doc

RAW_DOC = {
    "title": "Get order by id",
    "type": "REST",
    "location": "OrderController.java:36",
    "input_model": "Path parameter id.",
    "output_model": "Order JSON or 404.",
    "flow_mermaid": "flowchart TD\n  A --> B",
    "dependencies": [],
}


def test_parses_singly_encoded_json():
    doc = parse_entrypoint_doc(json.dumps(RAW_DOC))
    assert doc.title == "Get order by id"


def test_parses_doubly_encoded_json():
    # What a model-authored `JSON.stringify(outcome.value)` produces when
    # `outcome.value` (the task() result) is itself already a JSON string.
    double_encoded = json.dumps(json.dumps(RAW_DOC))
    doc = parse_entrypoint_doc(double_encoded)
    assert doc.title == "Get order by id"


def test_parses_result_wrapped_in_a_single_key_object():
    # Seen in practice: the model writes `{"result": "<json string>"}`.
    wrapped = json.dumps({"result": json.dumps(RAW_DOC)})
    doc = parse_entrypoint_doc(wrapped)
    assert doc.title == "Get order by id"


def test_parses_result_wrapped_and_then_double_encoded():
    wrapped = json.dumps(json.dumps({"result": json.dumps(RAW_DOC)}))
    doc = parse_entrypoint_doc(wrapped)
    assert doc.title == "Get order by id"


def test_raises_on_garbage():
    with pytest.raises(Exception):
        parse_entrypoint_doc("not json at all")


def test_candidates_to_json_writes_one_source_file_per_unique_file(tmp_path):
    from doku.state import candidates_to_json
    from doku.detectors import EntrypointCandidate

    (tmp_path / "Foo.java").write_text("class Foo { void bar() {} void baz() {} }")
    sources_dir = tmp_path / "sources"
    sources_dir.mkdir()
    candidates = [
        EntrypointCandidate(type="REST", file="Foo.java", line=1, class_name="Foo", method_name="bar", meta={}),
        EntrypointCandidate(type="REST", file="Foo.java", line=1, class_name="Foo", method_name="baz", meta={}),
    ]
    payload = candidates_to_json(tmp_path, candidates, sources_dir)

    # Both candidates share the same file -> the same source_ref, and only
    # one source file gets written for it.
    refs = {p["source_ref"] for p in payload}
    assert refs == {0}
    assert list(sources_dir.iterdir()) == [sources_dir / "0.json"]
    source = json.loads((sources_dir / "0.json").read_text())
    assert "class Foo" in "\n".join(source["source_lines"])
    assert "source_lines" not in payload[0]  # manifest itself stays source-free


def test_candidates_to_json_stores_source_as_a_list_of_lines(tmp_path):
    from doku.state import candidates_to_json
    from doku.detectors import EntrypointCandidate

    (tmp_path / "Foo.java").write_text("line one\nline two\nline three")
    sources_dir = tmp_path / "sources"
    sources_dir.mkdir()
    candidates = [
        EntrypointCandidate(type="REST", file="Foo.java", line=1, class_name="Foo", method_name="bar", meta={}),
    ]
    payload = candidates_to_json(tmp_path, candidates, sources_dir)

    source = json.loads((sources_dir / f"{payload[0]['source_ref']}.json").read_text())
    assert source["source_lines"] == ["line one", "line two", "line three"]


def test_candidates_to_json_truncates_oversized_files(tmp_path):
    from doku.state import MAX_SOURCE_CHARS, candidates_to_json
    from doku.detectors import EntrypointCandidate

    (tmp_path / "Big.java").write_text("x" * (MAX_SOURCE_CHARS + 500))
    sources_dir = tmp_path / "sources"
    sources_dir.mkdir()
    candidates = [
        EntrypointCandidate(type="REST", file="Big.java", line=1, class_name="Big", method_name="m", meta={}),
    ]
    payload = candidates_to_json(tmp_path, candidates, sources_dir)

    source = json.loads((sources_dir / f"{payload[0]['source_ref']}.json").read_text())
    joined = "\n".join(source["source_lines"])
    assert len(joined) <= MAX_SOURCE_CHARS + len("\n... (truncated)")
    assert joined.endswith("(truncated)")


def test_candidates_to_json_assigns_distinct_refs_per_file(tmp_path):
    from doku.state import candidates_to_json
    from doku.detectors import EntrypointCandidate

    (tmp_path / "A.java").write_text("a")
    (tmp_path / "B.java").write_text("b")
    sources_dir = tmp_path / "sources"
    sources_dir.mkdir()
    candidates = [
        EntrypointCandidate(type="REST", file="A.java", line=1, class_name="A", method_name="m", meta={}),
        EntrypointCandidate(type="REST", file="B.java", line=1, class_name="B", method_name="m", meta={}),
    ]
    payload = candidates_to_json(tmp_path, candidates, sources_dir)

    assert payload[0]["source_ref"] != payload[1]["source_ref"]
