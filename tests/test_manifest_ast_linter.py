"""AST tabanlı manifest kapı linter testleri — hardcoded True engeli.

Sıfır bypass politikası: manifest kapılarında sabit ``return True`` bulunamaz.
"""

from __future__ import annotations

from pathlib import Path

from tools.manifest_ast_linter import scan_file


def _write_tmp(tmp_path: Path, name: str, src: str) -> Path:
    p = tmp_path / name
    p.write_text(src, encoding="utf-8")
    return p


def test_ast_linter_rejects_hardcoded_true_gate(tmp_path: Path):
    """Manifest kapı fonksiyonu sabit True dönerse linter ihlal verir (exit 1 mantığı)."""
    src = """
def manifest_gate_active():
    return True
"""
    p = _write_tmp(tmp_path, "bad_gate.py", src)
    violations = scan_file(p)
    assert len(violations) == 1
    assert "hardcoded" in violations[0]["detail"].lower() or "return True" in violations[0]["detail"]
    assert violations[0]["function"] == "manifest_gate_active"


def test_ast_linter_passes_dynamic_gate(tmp_path: Path):
    """Dinamik doğrulama içeren manifest kapı geçer (ihlal yok)."""
    src = """
def manifest_gate_active():
    user = get_current_user()
    return user is not None and user.active
"""
    p = _write_tmp(tmp_path, "good_gate.py", src)
    violations = scan_file(p)
    assert len(violations) == 0


def test_ast_linter_passes_non_gate_hardcoded_true(tmp_path: Path):
    """Gate değilse hardcoded True uyarılmaz (strict mod dışında)."""
    src = """
def helper():
    return True
"""
    p = _write_tmp(tmp_path, "helper.py", src)
    violations = scan_file(p)
    assert len(violations) == 0


def test_ast_linter_strict_rejects_all_hardcoded_true(tmp_path: Path):
    """Strict modda tüm hardcoded True return'lar ihlaldir."""
    from tools.manifest_ast_linter import main

    src = """
def any_func():
    return True
"""
    p = _write_tmp(tmp_path, "any.py", src)
    rc = main([str(p), "--strict"])
    assert rc == 1
