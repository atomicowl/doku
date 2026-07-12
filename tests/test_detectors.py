from pathlib import Path

from doku.detectors import run_detectors
from doku.detectors.java_spring import JavaSpringDetector

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "sample_spring_app"


def _detect():
    return run_detectors(FIXTURE_ROOT, [JavaSpringDetector()])


def test_finds_all_three_entrypoint_types():
    candidates = _detect()
    types = {c.type for c in candidates}
    assert types == {"REST", "SOAP", "KAFKA"}


def test_finds_exactly_the_expected_methods():
    candidates = _detect()
    found = {(c.class_name, c.method_name, c.type) for c in candidates}
    assert found == {
        ("OrderController", "createOrder", "REST"),
        ("OrderController", "getOrder", "REST"),
        ("OrderEventsConsumer", "onMessage", "KAFKA"),
        ("PaymentEventsConsumer", "onPaymentCaptured", "KAFKA"),
        ("PaymentEventsConsumer", "onUnknownEvent", "KAFKA"),
        ("BillingEndpoint", "getInvoice", "SOAP"),
    }


def test_rest_route_and_http_method_are_combined_with_class_base_path():
    candidates = _detect()
    create_order = next(
        c
        for c in _detect()
        if c.class_name == "OrderController" and c.method_name == "createOrder"
    )
    assert create_order.meta["route"] == "/orders/{id}/submit"
    assert create_order.meta["http_method"] == "POST"

    get_order = next(
        c
        for c in candidates
        if c.class_name == "OrderController" and c.method_name == "getOrder"
    )
    assert get_order.meta["route"] == "/orders/{id}"
    assert get_order.meta["http_method"] == "GET"


def test_kafka_topic_and_group_id_extracted():
    candidates = _detect()
    consumer = next(
        c for c in candidates if c.class_name == "OrderEventsConsumer"
    )
    assert consumer.meta["topics"] == ["order-events"]
    assert consumer.meta["group_id"] == "orders-svc"


def test_class_level_kafka_listener_with_kafka_handlers():
    candidates = _detect()
    handlers = [c for c in candidates if c.class_name == "PaymentEventsConsumer"]
    assert {c.method_name for c in handlers} == {"onPaymentCaptured", "onUnknownEvent"}
    for handler in handlers:
        assert handler.type == "KAFKA"
        # topics/group come from the class-level @KafkaListener annotation
        assert handler.meta["topics"] == ["payment-events"]
        assert handler.meta["group_id"] == "payments-svc"


def test_soap_namespace_and_local_part_extracted():
    candidates = _detect()
    endpoint = next(c for c in candidates if c.type == "SOAP")
    assert endpoint.meta["local_part"] == "GetInvoiceRequest"
    # NAMESPACE is a constant reference, not a string literal -- best-effort only
    assert endpoint.meta["namespace"]


def test_candidates_are_sorted_by_file_then_line():
    candidates = _detect()
    keys = [(c.file, c.line) for c in candidates]
    assert keys == sorted(keys)


def test_autowired_fields_are_attached_to_candidate_meta():
    candidates = _detect()
    create_order = next(
        c
        for c in candidates
        if c.class_name == "OrderController" and c.method_name == "createOrder"
    )
    fields = {f["name"]: f["type"] for f in create_order.meta["autowired_fields"]}
    assert fields == {
        "orderRepository": "OrderRepository",
        "paymentClient": "PaymentClient",
        "eventsPublisher": "OrderEventsPublisher",
    }

    consumer = next(
        c for c in candidates if c.class_name == "OrderEventsConsumer"
    )
    consumer_fields = {f["name"] for f in consumer.meta["autowired_fields"]}
    assert consumer_fields == {"orderRepository", "shippingClient"}


def test_slug_is_stable_and_filesystem_safe():
    candidates = _detect()
    slugs = [c.slug for c in candidates]
    assert len(slugs) == len(set(slugs))
    for slug in slugs:
        assert " " not in slug
        assert slug == slug.lower() or "-" in slug
