from doku.models import DependencyRef, EntrypointDoc
from doku.render import (
    render_dependencies,
    render_entrypoint_markdown,
    render_errors,
    render_index,
)

DOC_A = EntrypointDoc(
    title="Create order",
    type="REST",
    location="OrderController.java:20",
    input_model="Path `id`, JSON body `OrderRequest`.",
    output_model="`OrderResponse` JSON or 404/402.",
    flow_mermaid="flowchart TD\n  A --> B",
    dependencies=[
        DependencyRef(kind="database", name="orders_db", usage="load/save Order"),
        DependencyRef(kind="rest_client", name="PaymentClient", usage="charge payment"),
    ],
)

DOC_B = EntrypointDoc(
    title="Order events consumer",
    type="KAFKA",
    location="OrderEventsConsumer.java:15",
    input_model="Kafka message payload (String).",
    output_model="No response; side effects only.",
    flow_mermaid="flowchart TD\n  A --> B",
    dependencies=[
        DependencyRef(kind="database", name="orders_db", usage="look up order"),
    ],
)


def test_render_entrypoint_markdown_includes_all_sections():
    md = render_entrypoint_markdown("rest-OrderController-createOrder", DOC_A)
    assert "# Create order" in md
    assert "**Type:** REST" in md
    assert "OrderController.java:20" in md
    assert "```mermaid" in md
    assert "flowchart TD" in md
    assert "| database | orders_db | load/save Order |" in md


def test_render_entrypoint_markdown_handles_no_dependencies():
    doc = DOC_A.model_copy(update={"dependencies": []})
    md = render_entrypoint_markdown("slug", doc)
    assert "_None found._" in md


def test_render_index_lists_all_entrypoints_with_links():
    md = render_index(
        [
            ("rest-OrderController-createOrder", DOC_A),
            ("kafka-OrderEventsConsumer-onMessage", DOC_B),
        ]
    )
    assert "[rest-OrderController-createOrder](entrypoints/rest-OrderController-createOrder.md)" in md
    assert "KAFKA" in md and "REST" in md


def test_render_dependencies_aggregates_across_entrypoints():
    md = render_dependencies(
        [
            ("rest-OrderController-createOrder", DOC_A),
            ("kafka-OrderEventsConsumer-onMessage", DOC_B),
        ]
    )
    # orders_db is used by both entrypoints -> appears once as a heading,
    # with both callers listed underneath.
    assert md.count("## orders_db (database)") == 1
    assert "[Create order]" in md
    assert "[Order events consumer]" in md


def test_render_dependencies_handles_empty_list():
    md = render_dependencies([])
    assert "_None found._" in md


def test_render_errors_lists_failures():
    md = render_errors([{"slug": "soap-Billing-getInvoice", "error": "timeout"}])
    assert "soap-Billing-getInvoice" in md
    assert "timeout" in md
