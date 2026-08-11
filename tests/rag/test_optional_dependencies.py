from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[2]


def _optional_dependencies() -> dict[str, list[str]]:
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return project["project"]["optional-dependencies"]


def test_rag_cpu_and_acceleration_extras_are_declared() -> None:
    extras = _optional_dependencies()

    assert {"rag", "rag-apple", "rag-cuda", "rag-openvino", "rag-directml"} <= set(
        extras
    )
    rag = "\n".join(extras["rag"]).lower()
    assert "onnxruntime" in rag
    assert "tokenizers" in rag
    assert "huggingface-hub" in rag
    assert "jieba" in rag
    assert "numpy" in rag
    assert "usearch" in rag

    assert "coremltools" in "\n".join(extras["rag-apple"]).lower()
    assert "onnxruntime-gpu" in "\n".join(extras["rag-cuda"]).lower()
    assert "onnxruntime-openvino" in "\n".join(extras["rag-openvino"]).lower()
    assert "onnxruntime-directml" in "\n".join(extras["rag-directml"]).lower()


def test_rag_packages_are_not_core_dependencies() -> None:
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    core = "\n".join(project["project"]["dependencies"]).lower()

    for package in (
        "onnxruntime",
        "tokenizers",
        "huggingface-hub",
        "jieba",
        "numpy",
        "usearch",
        "coremltools",
    ):
        assert package not in core


def test_base_package_imports_when_all_rag_packages_are_blocked() -> None:
    code = """
import importlib.abc
import sys

blocked = {
    "coremltools", "huggingface_hub", "jieba", "numpy", "onnxruntime",
    "tokenizers", "usearch"
}

class BlockRagPackages(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".", 1)[0] in blocked:
            raise ImportError(f"blocked optional RAG dependency: {fullname}")
        return None

sys.meta_path.insert(0, BlockRagPackages())
import nanobot
from nanobot.config.schema import Config

assert Config().rag.enabled is False
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
