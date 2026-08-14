#!/usr/bin/env python3
"""Generate a code -> docs -> data consistency report.

The checker is intentionally import-free. It inspects Python with ``ast``,
SQLite DDL with an in-memory database, the live data directory with read-only
SQLite connections, and Markdown by stable identifier lookup.
"""

from __future__ import annotations

import argparse
import ast
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src" / "rag_notion_kb"
DOCS_DIR = PROJECT_ROOT / "docs"
REPORT_PATH = DOCS_DIR / "consistency-report.md"


@dataclass
class ModelSpec:
    name: str
    fields: dict[str, str] = field(default_factory=dict)
    line: int = 0


@dataclass
class RouteSpec:
    method: str
    path: str
    handler: str
    line: int


@dataclass
class CommandSpec:
    name: str
    line: int


def parse_source(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def class_inherits(node: ast.ClassDef, names: set[str]) -> bool:
    for base in node.bases:
        if isinstance(base, ast.Name) and base.id in names:
            return True
        if isinstance(base, ast.Attribute) and base.attr in names:
            return True
    return False


def annotation_text(node: ast.AST | None) -> str:
    if node is None:
        return ""
    return ast.unparse(node)


def extract_pydantic_models(path: Path) -> list[ModelSpec]:
    tree = parse_source(path)
    models: list[ModelSpec] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if not class_inherits(node, {"BaseModel", "BaseSettings"}):
            continue
        fields: dict[str, str] = {}
        for item in node.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                fields[item.target.id] = annotation_text(item.annotation)
        models.append(ModelSpec(name=node.name, fields=fields, line=node.lineno))
    return models


def extract_exception_classes(path: Path) -> list[ModelSpec]:
    tree = parse_source(path)
    exceptions: list[ModelSpec] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if not class_inherits(node, {"Exception", "RagKbError"}):
            continue
        exceptions.append(ModelSpec(name=node.name, line=node.lineno))
    return exceptions


def extract_routes(path: Path) -> list[RouteSpec]:
    tree = parse_source(path)
    method_names = {"get", "post", "put", "patch", "delete"}
    routes: list[RouteSpec] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func
            method = ""
            if isinstance(func, ast.Attribute) and func.attr in method_names:
                method = func.attr
            elif isinstance(func, ast.Name) and func.id in method_names:
                method = func.id
            if not method or not decorator.args:
                continue
            route_path = decorator.args[0]
            if not isinstance(route_path, ast.Constant) or not isinstance(
                route_path.value, str
            ):
                continue
            routes.append(
                RouteSpec(
                    method=method.upper(),
                    path=route_path.value,
                    handler=node.name,
                    line=decorator.lineno,
                )
            )
    return routes


def _decorator_name(decorator: ast.AST) -> str:
    if isinstance(decorator, ast.Call):
        func = decorator.func
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
    return ""


def extract_commands(path: Path) -> list[CommandSpec]:
    tree = parse_source(path)
    commands: list[CommandSpec] = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        if any(_decorator_name(d) == "command" for d in node.decorator_list):
            commands.append(CommandSpec(name=node.name, line=node.lineno))
    return commands


def extract_mcp_tools(path: Path) -> list[CommandSpec]:
    tree = parse_source(path)
    tools: list[CommandSpec] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if any(_decorator_name(d) == "tool" for d in node.decorator_list):
            tools.append(CommandSpec(name=node.name, line=node.lineno))
    return tools


def extract_ddl_constants(path: Path) -> dict[str, str]:
    tree = parse_source(path)
    ddl: dict[str, str] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "_DDL"
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            ddl[path.name] = node.value.value
    return ddl


def table_from_ddl(ddl: str) -> dict[str, tuple[str, int, str]]:
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(ddl)
        rows = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table'"
        ).fetchall()
        tables: dict[str, dict[str, tuple[str, int, str]]] = {}
        for name, _ in rows:
            info = conn.execute(f'PRAGMA table_info("{name}")').fetchall()
            tables[name] = {
                row[1]: (row[2].upper(), int(bool(row[3])), str(row[4]))
                for row in info
            }
        return tables
    finally:
        conn.close()


def actual_tables(db_path: Path) -> dict[str, dict[str, tuple[str, int, str]]]:
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        tables: dict[str, dict[str, tuple[str, int, str]]] = {}
        for (name,) in rows:
            info = conn.execute(f'PRAGMA table_info("{name}")').fetchall()
            tables[name] = {
                row[1]: (row[2].upper(), int(bool(row[3])), str(row[4]))
                for row in info
            }
        return tables
    finally:
        conn.close()


def extract_milvus_fields(path: Path) -> list[tuple[str, str, str, int]]:
    tree = parse_source(path)
    fields: list[tuple[str, str, str, int]] = []
    constants: dict[str, int] = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, int)
        ):
            constants[node.targets[0].id] = node.value.value
    for node in tree.body:
        if (
            not isinstance(node, ast.Assign)
            or len(node.targets) != 1
            or not isinstance(node.targets[0], ast.Name)
            or node.targets[0].id != "FIELDS"
            or not isinstance(node.value, ast.List)
        ):
            continue
        for item in node.value.elts:
            if not isinstance(item, ast.Call):
                continue
            keywords = {keyword.arg: keyword.value for keyword in item.keywords}
            name_value = keywords.get("name")
            dtype_value = keywords.get("dtype")
            if not isinstance(name_value, ast.Constant) or not isinstance(
                dtype_value, ast.Attribute
            ):
                continue
            limit = 0
            for keyword_name in ("max_length", "dim"):
                value = keywords.get(keyword_name)
                if isinstance(value, ast.Constant) and isinstance(value.value, int):
                    limit = value.value
                    break
                if isinstance(value, ast.Name) and value.id in constants:
                    limit = constants[value.id]
                    break
            dtype = dtype_value.attr
            fields.append(
                (str(name_value.value), dtype, dtype, limit)
            )
    return fields


def extract_frontend_files(static_dir: Path) -> tuple[list[str], list[str], list[str]]:
    js = sorted(path.name for path in static_dir.glob("*.js"))
    components = sorted(
        path.name for path in (static_dir / "components").glob("*.vue")
    )
    views = sorted(path.name for path in (static_dir / "views").glob("*.vue"))
    return js, components, views


def extract_api_client_methods(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    return re.findall(r"^\s{4}(\w+):\s*(?:async\s*)?\(?.*?=>", source, re.MULTILINE)


def check_identifiers(
    specs: list[ModelSpec],
    doc_text: str,
    *,
    check_fields: bool = True,
) -> dict[str, object]:
    aligned: list[str] = []
    mismatched: list[dict[str, str]] = []
    missing: list[str] = []
    for spec in specs:
        if spec.name not in doc_text:
            missing.append(spec.name)
            continue
        if check_fields and all(field_name in doc_text for field_name in spec.fields):
            aligned.append(spec.name)
        elif not check_fields:
            aligned.append(spec.name)
        else:
            mismatched.append(
                {
                    "model": spec.name,
                    "missing_fields": ", ".join(
                        name for name in spec.fields if name not in doc_text
                    ),
                }
            )
    return {
        "aligned": aligned,
        "mismatched": mismatched,
        "missing": missing,
    }


def check_route_docs(routes: list[RouteSpec], doc_text: str) -> dict[str, object]:
    aligned: list[str] = []
    missing: list[dict[str, str]] = []
    for route in routes:
        if route.path in {"{full_path:path}", "/"}:
            aligned.append(route.path)
            continue
        if route.path in doc_text or f"{route.method} {route.path}" in doc_text:
            aligned.append(f"{route.method} {route.path}")
        else:
            missing.append(
                {
                    "method": route.method,
                    "path": route.path,
                    "handler": route.handler,
                    "line": str(route.line),
                }
            )
    return {"aligned": aligned, "missing": missing}


def check_simple_docs(specs: list[CommandSpec], doc_text: str) -> dict[str, object]:
    aligned = [spec.name for spec in specs if spec.name in doc_text]
    missing = [spec.name for spec in specs if spec.name not in doc_text]
    return {"aligned": aligned, "missing": missing}


def check_sqlite(
    source_ddl: dict[str, str],
    actual_db: Path,
    doc_text: str,
) -> dict[str, object]:
    source_tables: dict[str, dict[str, tuple[str, int, str]]] = {}
    for filename, ddl in source_ddl.items():
        for table_name, columns in table_from_ddl(ddl).items():
            source_tables[table_name] = columns

    actual = actual_tables(actual_db)
    aligned: list[str] = []
    mismatched: list[dict[str, object]] = []
    missing_in_data: list[str] = []
    missing_in_docs: list[dict[str, str]] = []

    for table_name, columns in source_tables.items():
        if table_name not in doc_text:
            missing_in_docs.append({"table": table_name})
            continue
        if table_name not in actual:
            missing_in_data.append(table_name)
            continue
        actual_columns = actual[table_name]
        if columns == actual_columns:
            aligned.append(table_name)
        else:
            differences: list[str] = []
            all_names = sorted(set(columns) | set(actual_columns))
            for name in all_names:
                if columns.get(name) != actual_columns.get(name):
                    differences.append(name)
            mismatched.append(
                {
                    "table": table_name,
                    "differences": ", ".join(differences),
                    "code": str(columns.get(name))
                    if differences
                    else "",
                    "data": str(actual_columns.get(name))
                    if differences
                    else "",
                }
            )
    return {
        "aligned": aligned,
        "mismatched": mismatched,
        "missing_in_data": missing_in_data,
        "missing_in_docs": missing_in_docs,
    }


def render_markdown(
    *,
    models: list[ModelSpec],
    configs: list[ModelSpec],
    exceptions: list[ModelSpec],
    routes: list[RouteSpec],
    commands: list[CommandSpec],
    tools: list[CommandSpec],
    milvus_fields: list[tuple[str, str, str, int]],
    js_files: list[str],
    components: list[str],
    views: list[str],
    api_methods: list[str],
    sqlite_result: dict[str, object],
    model_result: dict[str, object],
    config_result: dict[str, object],
    exception_result: dict[str, object],
    route_result: dict[str, object],
    command_result: dict[str, object],
    tool_result: dict[str, object],
    milvus_result: dict[str, object],
    active_docs: list[str],
    organization: dict[str, bool],
    data_dir: Path,
) -> str:
    def status(section: dict[str, object]) -> tuple[int, int]:
        mismatched = len(section.get("mismatched", []))
        missing = len(section.get("missing", []))
        return mismatched, missing

    model_mismatch, model_missing = status(model_result)
    config_mismatch, config_missing = status(config_result)
    exception_mismatch, exception_missing = status(exception_result)
    route_missing = len(route_result.get("missing", []))
    command_missing = len(command_result.get("missing", []))
    tool_missing = len(tool_result.get("missing", []))
    milvus_missing = len(milvus_result.get("missing", []))
    sqlite_mismatch = len(sqlite_result.get("mismatched", []))
    sqlite_data_missing = len(sqlite_result.get("missing_in_data", []))
    sqlite_doc_missing = len(sqlite_result.get("missing_in_docs", []))

    lines = [
        "# Consistency Report",
        "",
        f"> Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC",
        f"> Data Dir: `{data_dir}`",
        "",
        "## Summary",
        "",
        "| Dimension | Aligned | Mismatched | Missing in Docs | Missing in Data |",
        "|-----------|--------:|-----------:|----------------:|----------------:|",
        f"| Data Models | {len(model_result['aligned'])} | {model_mismatch} | {model_missing} | 0 |",
        f"| Config Groups | {len(config_result['aligned'])} | {config_mismatch} | {config_missing} | 0 |",
        f"| Exceptions | {len(exception_result['aligned'])} | {exception_mismatch} | {exception_missing} | 0 |",
        f"| SQLite Schema | {len(sqlite_result['aligned'])} | {sqlite_mismatch} | {sqlite_doc_missing} | {sqlite_data_missing} |",
        f"| API Routes | {len(route_result['aligned'])} | 0 | {route_missing} | 0 |",
        f"| CLI Commands | {len(command_result['aligned'])} | 0 | {command_missing} | 0 |",
        f"| MCP Tools | {len(tool_result['aligned'])} | 0 | {tool_missing} | 0 |",
        f"| Milvus Fields | {len(milvus_result['aligned'])} | 0 | {milvus_missing} | 0 |",
        f"| Frontend Files | {len(js_files) + len(components) + len(views)} | 0 | 0 | 0 |",
        "",
        "## Action Items",
        "",
    ]

    total_issues = (
        model_mismatch
        + model_missing
        + config_mismatch
        + config_missing
        + exception_mismatch
        + exception_missing
        + route_missing
        + command_missing
        + tool_missing
        + milvus_missing
        + sqlite_mismatch
        + sqlite_data_missing
        + sqlite_doc_missing
        + (0 if organization["active_count_ok"] else 1)
    )
    if total_issues == 0:
        lines.append("None. Code, documentation, and checked data schema are aligned.")
        lines.append("")

    if model_missing:
        lines.append(f"- [P0] Backend.md: add models {', '.join(map(repr, model_result['missing']))}")
    if model_mismatch:
        for item in model_result["mismatched"]:
            lines.append(
                f"- [P0] Backend.md: add fields "
                f"{item['missing_fields']} to {item['model']}"
            )
    if config_missing:
        lines.append(
            f"- [P0] Backend.md: add config groups {', '.join(map(repr, config_result['missing']))}"
        )
    if config_mismatch:
        for item in config_result["mismatched"]:
            lines.append(
                f"- [P0] Backend.md: add fields "
                f"{item['missing_fields']} to {item['model']}"
            )
    if exception_missing:
        lines.append(
            f"- [P1] Backend.md: add exceptions {', '.join(map(repr, exception_result['missing']))}"
        )
    if route_missing:
        for item in route_result["missing"]:
            lines.append(
                f"- [P0] Backend.md: document {item['method']} {item['path']} "
                f"({item['handler']}, line {item['line']})"
            )
    if command_missing:
        lines.append(
            f"- [P0] Backend.md or Manual.md: document CLI commands "
            f"{', '.join(map(repr, command_result['missing']))}"
        )
    if tool_missing:
        lines.append(
            f"- [P0] Backend.md: document MCP tools "
            f"{', '.join(map(repr, tool_result['missing']))}"
        )
    if milvus_missing:
        lines.append(
            f"- [P0] Backend.md: document Milvus fields "
            f"{', '.join(map(repr, milvus_result['missing']))}"
        )
    if sqlite_doc_missing:
        for item in sqlite_result["missing_in_docs"]:
            lines.append(f"- [P1] Backend.md: document SQLite table {item['table']}")
    if sqlite_data_missing:
        lines.append(
            f"- [P0] Data schema: initialize tables "
            f"{', '.join(map(repr, sqlite_result['missing_in_data']))}"
        )
    if sqlite_mismatch:
        for item in sqlite_result["mismatched"]:
            lines.append(
                f"- [P0] Data schema: align {item['table']} columns "
                f"({item['differences']})"
            )
    if not organization["active_count_ok"]:
        lines.append("- [P1] Docs organization: keep no more than 7 active root documents")

    lines.extend(
        [
            "",
            "## Inventory",
            "",
            f"### Pydantic Models ({len(models)})",
            "",
            "| Model | Fields | Fields |",
            "|-------|-------:|--------|",
        ]
    )
    for model in models:
        lines.append(
            f"| {model.name} | {len(model.fields)} | "
            f"{', '.join(model.fields) or '-'} |"
        )

    lines.extend(
        [
            "",
            f"### Config Groups ({len(configs)})",
            "",
            "| Group | Fields | Fields |",
            "|-------|-------:|--------|",
        ]
    )
    for group in configs:
        lines.append(
            f"| {group.name} | {len(group.fields)} | "
            f"{', '.join(group.fields) or '-'} |"
        )

    lines.extend(
        [
            "",
            f"### Exceptions ({len(exceptions)})",
            "",
            f"- {', '.join(item.name for item in exceptions) or '-'}",
            "",
            f"### API Routes ({len(routes)})",
            "",
            "| Method | Path | Handler | Line | Docs |",
            "|--------|------|---------|-----:|------|",
        ]
    )
    for route in routes:
        status_text = (
            "✅"
            if route.method.upper() + " " + route.path
            not in route_result.get("missing_paths", set())
            else "❌"
        )
        lines.append(
            f"| {route.method} | `{route.path}` | {route.handler} | "
            f"{route.line} | {status_text} |"
        )

    lines.extend(
        [
            "",
            f"### CLI Commands ({len(commands)})",
            "",
            f"- {', '.join(item.name for item in commands) or '-'}",
            "",
            f"### MCP Tools ({len(tools)})",
            "",
            f"- {', '.join(item.name for item in tools) or '-'}",
            "",
            f"### Milvus Fields ({len(milvus_fields)})",
            "",
            "| Field | Type | Limit/Dim |",
            "|-------|------|----------:|",
        ]
    )
    for name, dtype, _, limit in milvus_fields:
        lines.append(f"| {name} | {dtype} | {limit} |")

    lines.extend(
        [
            "",
            "### Frontend Static Files",
            "",
            "| Kind | Files |",
            "|------|-------|",
            f"| JS | {', '.join(js_files) or '-'} |",
            f"| Components | {', '.join(components) or '-'} |",
            f"| Views | {', '.join(views) or '-'} |",
            "",
            f"### Frontend API Client Methods ({len(api_methods)})",
            "",
            f"- {', '.join(api_methods) or '-'}",
            "",
            "### Docs Organization",
            "",
            f"- Active root documents: `{', '.join(active_docs) or '-'}`",
            f"- Active count within limit: "
            f"{'✅' if organization['active_count_ok'] else '❌'}",
            f"- Archived directory: "
            f"{'✅' if organization['archived_exists'] else '❌'}",
            f"- Dev directory: {'✅' if organization['dev_exists'] else '❌'}",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path.home() / ".rag_kb",
        help="Runtime data directory (default: ~/.rag_kb)",
    )
    parser.add_argument(
        "--fail-on-mismatch",
        action="store_true",
        help="Exit 1 when documentation or checked data schema is misaligned",
    )
    args = parser.parse_args()

    data_dir = args.data_dir.expanduser()
    db_path = data_dir / "sync_state.db"
    backend_text = (DOCS_DIR / "Backend.md").read_text(encoding="utf-8")
    frontend_text = (DOCS_DIR / "Frontend.md").read_text(encoding="utf-8")

    models = extract_pydantic_models(SRC_DIR / "models.py")
    configs = extract_pydantic_models(SRC_DIR / "config.py")
    exceptions = extract_exception_classes(SRC_DIR / "exceptions.py")
    routes = extract_routes(SRC_DIR / "web" / "web_server.py")
    commands = extract_commands(SRC_DIR / "cli.py")
    tools = extract_mcp_tools(SRC_DIR / "mcp_server.py")
    milvus_fields = extract_milvus_fields(SRC_DIR / "storage" / "schema.py")

    source_ddl: dict[str, str] = {}
    for path in [
        SRC_DIR / "storage" / "sync_state.py",
        SRC_DIR / "storage" / "root_store.py",
        SRC_DIR / "storage" / "tree_cache.py",
        SRC_DIR / "services" / "sync_progress.py",
        SRC_DIR / "storage" / "search_history_store.py",
    ]:
        source_ddl.update(extract_ddl_constants(path))

    model_result = check_identifiers(models, backend_text)
    config_result = check_identifiers(configs, backend_text)
    exception_result = check_identifiers(exceptions, backend_text, check_fields=False)
    route_result = check_route_docs(routes, backend_text)
    route_result["missing_paths"] = {
        f"{item['method']} {item['path']}" for item in route_result["missing"]
    }
    command_result = check_simple_docs(commands, backend_text)
    tool_result = check_simple_docs(tools, backend_text)
    milvus_result = check_simple_docs(
        [CommandSpec(name=name, line=0) for name, _, _, _ in milvus_fields],
        backend_text,
    )
    sqlite_result = check_sqlite(source_ddl, db_path, backend_text)

    js_files, components, views = extract_frontend_files(SRC_DIR / "web" / "static")
    api_methods = extract_api_client_methods(SRC_DIR / "web" / "static" / "api.js")
    frontend_missing = [
        name for name in js_files + components + views if name not in frontend_text
    ]

    known_active = {
        "README.md",
        "Spec.md",
        "Architecture.md",
        "Backend.md",
        "Frontend.md",
        "Stand.md",
        "Manual.md",
    }
    active_docs = sorted(
        path.name
        for path in DOCS_DIR.glob("*.md")
        if path.name not in {"consistency-report.md"}
    )
    organization = {
        "active_count_ok": len(active_docs) <= len(known_active),
        "archived_exists": (DOCS_DIR / "archived").is_dir(),
        "dev_exists": (DOCS_DIR / "dev").is_dir(),
    }

    report = render_markdown(
        models=models,
        configs=configs,
        exceptions=exceptions,
        routes=routes,
        commands=commands,
        tools=tools,
        milvus_fields=milvus_fields,
        js_files=js_files,
        components=components,
        views=views,
        api_methods=api_methods,
        sqlite_result=sqlite_result,
        model_result=model_result,
        config_result=config_result,
        exception_result=exception_result,
        route_result=route_result,
        command_result=command_result,
        tool_result=tool_result,
        milvus_result=milvus_result,
        active_docs=active_docs,
        organization=organization,
        data_dir=data_dir,
    )
    if frontend_missing:
        report += (
            "\n\n### Frontend Documentation Gaps\n\n"
            + "\n".join(f"- `{name}` is not mentioned in Frontend.md" for name in frontend_missing)
            + "\n"
        )
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"Wrote {REPORT_PATH}")
    print(
        "Summary: "
        f"models={len(model_result['aligned'])}/{len(models)}, "
        f"routes={len(route_result['aligned'])}/{len(routes)}, "
        f"sqlite={len(sqlite_result['aligned'])}/{len(sqlite_result['aligned']) + len(sqlite_result['mismatched']) + len(sqlite_result['missing_in_data'])}"
    )

    issues = (
        len(model_result["mismatched"])
        + len(model_result["missing"])
        + len(config_result["mismatched"])
        + len(config_result["missing"])
        + len(exception_result["mismatched"])
        + len(exception_result["missing"])
        + len(route_result["missing"])
        + len(command_result["missing"])
        + len(tool_result["missing"])
        + len(milvus_result["missing"])
        + len(sqlite_result["mismatched"])
        + len(sqlite_result["missing_in_data"])
        + len(sqlite_result["missing_in_docs"])
        + len(frontend_missing)
        + (0 if organization["active_count_ok"] else 1)
    )
    if args.fail_on_mismatch and issues:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
