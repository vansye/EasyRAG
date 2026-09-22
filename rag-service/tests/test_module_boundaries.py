"""Executable ownership rules for the modular backend, including relative imports."""

import ast
import importlib.util
from pathlib import Path

import pytest


APP = Path(__file__).resolve().parents[1] / "app"
MODULES = ("knowledge", "retrieval", "qa", "answer_models")


def imported_paths(source: str, package: str) -> list[str]:
    paths = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            paths.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            name = "." * node.level + (node.module or "")
            base = importlib.util.resolve_name(name, package) if node.level else name
            paths.extend(f"{base}.{alias.name}" for alias in node.names)
    return paths


def violations(source: str, package: str, owner: str | None, *, evaluation=False) -> list[str]:
    denied = []
    for target in imported_paths(source, package):
        root = target.split(".")[0]
        if owner:
            own = f"app.modules.{owner}."
            if target.startswith("app.") and not target.startswith(own):
                denied.append(target)
            if root in {"fastapi", "starlette"}:
                denied.append(target)
            if root in {"sqlalchemy", "pymysql", "alembic"} and owner != "knowledge":
                denied.append(target)
            if root in {"chromadb", "tokenizers", "jieba", "rank_bm25"} and owner != "retrieval":
                denied.append(target)
            if (root.startswith("langchain") or root in {"openai", "httpx2"}) and owner != "answer_models":
                denied.append(target)
        else:
            if target == "app.modules" or target.startswith("app.modules."):
                parts = target.split(".")
                if len(parts) < 4 or parts[3] != "public":
                    denied.append(target)
            if not evaluation and (root in {"sqlalchemy", "pymysql", "alembic", "chromadb", "tokenizers", "jieba", "rank_bm25",
                                            "openai", "httpx2"}
                                   or root.startswith("langchain")):
                denied.append(target)
    return denied


@pytest.mark.parametrize("source, package, owner", [
    ("from app.modules.retrieval.public import Retrieval", "app.modules.qa", "qa"),
    ("from ..retrieval import public", "app.modules.qa", "qa"),
    ("from app.application import gate", "app.modules.knowledge", "knowledge"),
    ("from fastapi import HTTPException", "app.modules.answer_models", "answer_models"),
    ("import sqlalchemy", "app.modules.retrieval", "retrieval"),
    ("import jieba", "app.modules.qa", "qa"),
    ("from rank_bm25 import BM25Okapi", "app.application", None),
    ("from langchain_core.messages import HumanMessage", "app.modules.qa", "qa"),
    ("from app.modules.knowledge._database import engine", "app.application", None),
    ("from ..modules import knowledge", "app.application", None),
    ("import app.modules", "app.application", None),
    ("from app import modules", "app.application", None),
    ("from .. import modules", "app.application", None),
    ("import sqlalchemy", "app.application", None),
    ("import chromadb", "app.application", None),
    ("from openai import OpenAI", "app.application", None),
])
def test_detects_forbidden_dependencies(source, package, owner):
    assert violations(source, package, owner)


@pytest.mark.parametrize("source, package, owner", [
    ("from ._database import Database", "app.modules.knowledge", "knowledge"),
    ("from app.modules.knowledge.public import Knowledge", "app.application", None),
    ("from ..modules.retrieval.public import Retrieval", "app.application", None),
    ("from typing import Protocol", "app.modules.qa", "qa"),
])
def test_allows_owned_implementation_and_public_consumers(source, package, owner):
    assert not violations(source, package, owner)


def test_business_modules_exist_and_obey_ownership():
    for owner in MODULES:
        directory = APP / "modules" / owner
        assert (directory / "public.py").is_file(), f"missing public boundary: {owner}"
        for path in directory.rglob("*.py"):
            package = ".".join(path.parent.relative_to(APP.parent).parts)
            assert not violations(path.read_text(encoding="utf-8-sig"), package, owner), path


def test_application_uses_only_public_module_entrypoints():
    directory = APP / "application"
    assert directory.is_dir(), "missing application composition boundary"
    paths = [*directory.rglob("*.py"), *APP.glob("*.py")]
    for path in paths:
        package = ".".join(path.parent.relative_to(APP.parent).parts)
        assert not violations(path.read_text(encoding="utf-8-sig"), package, None), path


def test_offline_tools_use_only_public_business_entrypoints():
    for path in (APP.parent / "scripts").rglob("*.py"):
        assert not violations(path.read_text(encoding="utf-8-sig"), "scripts", None, evaluation=True), path
