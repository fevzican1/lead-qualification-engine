"""AST tabanlı statik analiz linter — manifest kapılarındaki hardcoded True'engeli.

Rapor gereksinimi:
  Python'un dahili ``ast`` modülü kullanılarak kaynak koddaki ``ast.Assign`` ve
  ``ast.Return`` düğümleri taranır. Bir manifest kapı fonksiyonu dinamik bir
  doğrulama çalıştırmak yerine sabit bir ``ast.Constant(value=True)`` ifadesi
  döndürdüğü tespit edildiğinde CI/CD boru hattı derlemeyi reddeder.

Kullanım:
  python tools/manifest_ast_linter.py <dosya.py> [--strict]
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Any

# Manifest kapı fonksiyonu imzası ipuçları (ad veya decoyator kalıbı).
GATE_NAME_HINTS = ("gate", "manifest", "kapı", "kapı_", "_gate", "is_", "can_")


def _is_hardcoded_true(node: ast.AST) -> bool:
    """Düğüm sabit bir ``True`` sabitliği mi? (ast.Constant(value=True))"""
    return isinstance(node, ast.Constant) and node.value is True


def _has_dynamic_validation(body: list[ast.stmt]) -> bool:
    """Fonksiyon gövdesinde dinamik bir doğrulama çağrısı var mı?

    Dinamik olarak sayılan desenler:
      - herhangi bir fonksiyon çağrısı (``func()``)
      - karşılaştırma (``a == b``, ``x in y``)
      - bool işlemi (``and``, ``or``)
      - if koşulu
    Sadece ``return True`` (veya atama) ise dinamik yoktur.
    """
    for stmt in body:
        if isinstance(stmt, ast.Return):
            if _is_hardcoded_true(stmt.value):
                continue  # hardcoded true — dinamik değil
            return True  # return ifadesi ama sabit True değil → dinamik olabilir
        if isinstance(stmt, ast.If):
            return True
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            return True
        if isinstance(stmt, ast.Assert):
            return True
    return False


def scan_file(path: Path, strict: bool = False) -> list[dict[str, Any]]:
    """Belirtilen Python dosyasındaki manifest kapı ihlallerini tarar.

    İhlal: bir fonksiyon sadece ``return True`` (hardcoded) döndürür ve
    gövdesinde dinamik doğrulama yoksa (veya fonksiyon adı gate/manifest
    ipucunu içeriyorsa) ihlal sayılır.
    """
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(path))
    violations: list[dict[str, Any]] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        name = node.name.lower()
        is_gate_like = any(h in name for h in GATE_NAME_HINTS)

        # Fonksiyon gövdesini kontrol et.
        has_dynamic = _has_dynamic_validation(node.body)

        # Yalnızca "return True" hardcoded ve dinamik yok → ihlal.
        only_returns_true = (
            len(node.body) == 1
            and isinstance(node.body[0], ast.Return)
            and _is_hardcoded_true(node.body[0].value)
        )

        if is_gate_like and only_returns_true and not has_dynamic:
            violations.append(
                {
                    "file": str(path),
                    "function": node.name,
                    "line": node.lineno,
                    "detail": (
                        f"Manifest kapı '{node.name}' sabit ``return True`` ile hardcoded — "
                        f"dinamik doğrulama eksik. CI'de reddedilir."
                    ),
                }
            )

        # Genel kural: herhangi bir fonksiyon hardcoded True return ediyorsa ve
        # strict mod açıksa uyar.
        if strict and only_returns_true and not has_dynamic and not is_gate_like:
            violations.append(
                {
                    "file": str(path),
                    "function": node.name,
                    "line": node.lineno,
                    "detail": (
                        f"Fonksiyon '{node.name}' hardcoded ``return True`` içeriyor — "
                        f"dynamic doğrulama eksik."
                    ),
                }
            )

    return violations


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Manifest kapılarında hardcoded True tarayıcı (AST statik analiz)"
    )
    parser.add_argument("paths", nargs="+", help="Taranacak Python dosyası/dizini")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Tüm hardcoded-True return'ları ihlal say (gate dışındakiler de)",
    )
    args = parser.parse_args(argv)

    all_violations: list[dict[str, Any]] = []
    for p in args.paths:
        path = Path(p)
        if path.is_dir():
            for py in sorted(path.rglob("*.py")):
                all_violations.extend(scan_file(py, strict=args.strict))
        else:
            all_violations.extend(scan_file(path, strict=args.strict))

    if all_violations:
        print("AST MANIFEST LİNTER — İHLAL BULUNDU (derleme engellenir):")
        for v in all_violations:
            print(f"  {v['file']}:{v['line']}  func={v['function']}  — {v['detail']}")
        return 1

    print("AST MANIFEST LİNTER — TÜM KAPIDA DINAMİK DOĞRULAMA VAR (geçti)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
