"""Detector for Java/Spring (and plain JAX-WS) entrypoints.

Covers, via tree-sitter over the real Java grammar (robust to multi-line
annotations/signatures, unlike regex):

- REST: `@RestController`/`@Controller` classes, `@*Mapping` methods.
- SOAP: Spring-WS `@Endpoint`/`@PayloadRoot`, or JAX-WS `@WebService`.
- Kafka: `@KafkaListener` methods, or `@KafkaHandler` methods inside a
  class-level `@KafkaListener` class.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tree_sitter_java as tsjava
from tree_sitter import Language, Node, Parser

from doku.detectors import EntrypointCandidate, is_excluded_dir

_LANGUAGE = Language(tsjava.language())

_REST_MAPPING_ANNOTATIONS = {
    "RequestMapping": None,  # HTTP method comes from the `method` element, if any
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PUT",
    "DeleteMapping": "DELETE",
    "PatchMapping": "PATCH",
}

_INJECTION_ANNOTATIONS = {"Autowired", "Inject", "Resource"}


def _new_parser() -> Parser:
    return Parser(_LANGUAGE)


def _text(node: Node) -> str:
    return node.text.decode("utf-8")


def _string_literal_value(node: Node) -> str:
    text = _text(node)
    return text[1:-1] if len(text) >= 2 and text[0] in "\"'" else text


def _annotation_value_to_python(node: Node) -> Any:
    if node.type == "string_literal":
        return _string_literal_value(node)
    if node.type == "element_value_array_initializer":
        return [
            _annotation_value_to_python(c)
            for c in node.children
            if c.type not in ("{", "}", ",")
        ]
    # enum constants (e.g. `RequestMethod.POST`), identifiers, numbers, etc.
    return _text(node)


def _parse_annotation_args(args_node: Node | None) -> dict[str, Any]:
    """Parse an `annotation_argument_list` into a dict.

    A bare positional value (`@RequestMapping("/orders")`) is stored under
    `"value"`; `key = value` pairs keep their own key.
    """
    result: dict[str, Any] = {}
    if args_node is None:
        return result
    positional: list[Any] = []
    for child in args_node.children:
        if child.type == "element_value_pair":
            key_node = child.child_by_field_name("key")
            value_node = child.child_by_field_name("value")
            if key_node is not None and value_node is not None:
                result[_text(key_node)] = _annotation_value_to_python(value_node)
        elif child.type not in ("(", ")", ","):
            positional.append(_annotation_value_to_python(child))
    if positional:
        result.setdefault("value", positional[0] if len(positional) == 1 else positional)
    return result


def _modifiers_of(declaration_node: Node) -> Node | None:
    return next((c for c in declaration_node.children if c.type == "modifiers"), None)


def _annotations_of(declaration_node: Node) -> dict[str, dict[str, Any]]:
    """Map annotation name -> parsed arguments, for a class/method/interface node."""
    modifiers = _modifiers_of(declaration_node)
    if modifiers is None:
        return {}
    annotations: dict[str, dict[str, Any]] = {}
    for child in modifiers.children:
        if child.type not in ("marker_annotation", "annotation"):
            continue
        name_node = child.child_by_field_name("name")
        if name_node is None:
            continue
        args_node = child.child_by_field_name("arguments")
        annotations[_text(name_node)] = _parse_annotation_args(args_node)
    return annotations


def _autowired_fields(class_body: Node) -> list[dict[str, str]]:
    """Injected fields (`@Autowired`/`@Inject`/`@Resource`) of a class.

    Handed to the documenter subagent as an explicit checklist: classifying
    "does this method use field X" is a much more reliable task for an LLM
    than spontaneously noticing every dependency unprompted.
    """
    fields: list[dict[str, str]] = []
    for member in class_body.children:
        if member.type != "field_declaration":
            continue
        if not (_INJECTION_ANNOTATIONS & _annotations_of(member).keys()):
            continue
        type_node = member.child_by_field_name("type")
        type_name = _text(type_node) if type_node is not None else "?"
        for declarator in member.children:
            if declarator.type != "variable_declarator":
                continue
            name_node = declarator.child_by_field_name("name")
            if name_node is not None:
                fields.append({"name": _text(name_node), "type": type_name})
    return fields


def _is_public(declaration_node: Node) -> bool:
    modifiers = _modifiers_of(declaration_node)
    if modifiers is None:
        return False
    return any(c.type == "public" for c in modifiers.children)


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


def _http_method_for(annotation_name: str, args: dict[str, Any]) -> str:
    fixed = _REST_MAPPING_ANNOTATIONS.get(annotation_name)
    if fixed:
        return fixed
    method = args.get("method")
    if method:
        candidates = _as_list(method)
        # enum constants render as `RequestMethod.POST` -> take the last segment
        return candidates[0].rsplit(".", 1)[-1]
    return "ANY"


def _join_paths(base: str, sub: str) -> str:
    base = base.strip("/")
    sub = sub.strip("/")
    joined = "/".join(p for p in (base, sub) if p)
    return "/" + joined if joined else "/"


def _class_like_declarations(root: Node) -> list[Node]:
    found: list[Node] = []

    def walk(node: Node) -> None:
        if node.type in ("class_declaration", "interface_declaration"):
            found.append(node)
        for child in node.children:
            walk(child)

    walk(root)
    return found


@dataclass(frozen=True)
class _ClassContext:
    """The class-level facts a method needs to be classified."""

    name: str
    base_path: str  # class-level @RequestMapping path, "" if none
    is_rest_controller: bool
    is_web_service: bool
    kafka_listener_args: dict[str, Any] | None  # class-level @KafkaListener, if any


class JavaSpringDetector:
    """Detector for Java/Spring REST + SOAP-Endpoint entrypoints, and Kafka listeners."""

    def detect(self, repo_root: Path) -> list[EntrypointCandidate]:
        candidates: list[EntrypointCandidate] = []
        parser = _new_parser()
        for java_file in repo_root.rglob("*.java"):
            if is_excluded_dir(java_file.relative_to(repo_root)):
                continue
            source = java_file.read_bytes()
            tree = parser.parse(source)
            rel_path = java_file.relative_to(repo_root).as_posix()
            candidates.extend(self._detect_in_file(tree.root_node, rel_path))
        return candidates

    def _detect_in_file(
        self, root: Node, rel_path: str
    ) -> list[EntrypointCandidate]:
        found: list[EntrypointCandidate] = []
        for class_node in _class_like_declarations(root):
            name_node = class_node.child_by_field_name("name")
            body_node = class_node.child_by_field_name("body")
            if name_node is None or body_node is None:
                continue
            class_annotations = _annotations_of(class_node)
            base_path = _as_list(
                class_annotations.get("RequestMapping", {}).get("value")
            )
            context = _ClassContext(
                name=_text(name_node),
                base_path=base_path[0] if base_path else "",
                is_rest_controller=(
                    "RestController" in class_annotations or "Controller" in class_annotations
                ),
                is_web_service="WebService" in class_annotations,
                kafka_listener_args=class_annotations.get("KafkaListener"),
            )
            autowired_fields = _autowired_fields(body_node)

            for member in body_node.children:
                if member.type != "method_declaration":
                    continue
                candidate = self._classify_method(rel_path, context, member)
                if candidate is not None:
                    if autowired_fields:
                        candidate.meta["autowired_fields"] = autowired_fields
                    found.append(candidate)
        return found

    def _classify_method(
        self, rel_path: str, cls: _ClassContext, method_node: Node
    ) -> EntrypointCandidate | None:
        method_name_node = method_node.child_by_field_name("name")
        if method_name_node is None:
            return None
        method_name = _text(method_name_node)
        line = method_node.start_point[0] + 1
        method_annotations = _annotations_of(method_node)

        # Kafka consumer: either a method-level @KafkaListener, or an
        # @KafkaHandler method inside a class-level @KafkaListener class
        # (topics/group live on the class annotation in that form).
        args = method_annotations.get("KafkaListener")
        if args is None and "KafkaHandler" in method_annotations:
            args = cls.kafka_listener_args
        if args is not None:
            topics = _as_list(args.get("topics") or args.get("value"))
            return EntrypointCandidate(
                type="KAFKA",
                file=rel_path,
                line=line,
                class_name=cls.name,
                method_name=method_name,
                meta={"topics": topics, "group_id": args.get("groupId")},
            )

        # Spring-WS SOAP endpoint
        if "PayloadRoot" in method_annotations:
            args = method_annotations["PayloadRoot"]
            return EntrypointCandidate(
                type="SOAP",
                file=rel_path,
                line=line,
                class_name=cls.name,
                method_name=method_name,
                meta={
                    "namespace": args.get("namespace"),
                    "local_part": args.get("localPart"),
                },
            )

        # REST controller method
        if cls.is_rest_controller:
            for annotation_name in _REST_MAPPING_ANNOTATIONS:
                if annotation_name not in method_annotations:
                    continue
                args = method_annotations[annotation_name]
                method_path = _as_list(args.get("value") or args.get("path"))
                method_path = method_path[0] if method_path else ""
                return EntrypointCandidate(
                    type="REST",
                    file=rel_path,
                    line=line,
                    class_name=cls.name,
                    method_name=method_name,
                    meta={
                        "route": _join_paths(cls.base_path, method_path),
                        "http_method": _http_method_for(annotation_name, args),
                    },
                )

        # JAX-WS SOAP: every public method is an operation unless excluded
        if cls.is_web_service and _is_public(method_node):
            web_method_args = method_annotations.get("WebMethod", {})
            if web_method_args.get("exclude") in (True, "true"):
                return None
            return EntrypointCandidate(
                type="SOAP",
                file=rel_path,
                line=line,
                class_name=cls.name,
                method_name=method_name,
                meta={"operation_name": web_method_args.get("operationName")},
            )

        return None
