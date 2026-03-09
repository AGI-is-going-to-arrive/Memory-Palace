import builtins
import importlib.util
from pathlib import Path

import api.utils as review_utils


def test_utils_module_imports_without_diff_match_patch(monkeypatch) -> None:
    module_path = Path(__file__).resolve().parents[1] / "api" / "utils.py"
    spec = importlib.util.spec_from_file_location("review_utils_missing_dep", module_path)
    assert spec is not None
    assert spec.loader is not None

    original_import = builtins.__import__

    def _import_without_diff_match_patch(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "diff_match_patch":
            raise ModuleNotFoundError("No module named 'diff_match_patch'")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _import_without_diff_match_patch)

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    diff_html, diff_unified, summary = module.get_text_diff("old text", "new text")

    assert module.DiffMatchPatch is None
    assert "<table class=\"diff\"" in diff_html
    assert "--- old_version" in diff_unified
    assert "变化" in summary


def test_get_text_diff_falls_back_when_optional_dependency_unavailable(
    monkeypatch,
) -> None:
    monkeypatch.setattr(review_utils, "DiffMatchPatch", None)

    diff_html, diff_unified, summary = review_utils.get_text_diff(
        "line one\nline two\n",
        "line one\nline three\n",
    )

    assert "<table class=\"diff\"" in diff_html
    assert "@@" in diff_unified
    assert "变化" in summary
