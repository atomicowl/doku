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
