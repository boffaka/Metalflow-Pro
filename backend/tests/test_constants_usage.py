"""Verify all engines use centralized constants (no local redefinitions)."""
import ast
import pathlib

ENGINES_DIR = pathlib.Path(__file__).parent.parent / "engines"

FORBIDDEN_REDEFINITIONS = {
    "TROY_OZ_PER_GRAM",
    "WATER_SG",
    "FARADAY_C_MOL",
    "M_AU_G_MOL",
    "AU_ELECTRONS",
    "AP_FACTOR_PYRITE",
}


def test_no_local_constant_redefinitions():
    violations = []
    for py_file in ENGINES_DIR.glob("*.py"):
        if py_file.name == "__init__.py":
            continue
        source = py_file.read_text()
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in FORBIDDEN_REDEFINITIONS:
                        violations.append(f"{py_file.name}:{node.lineno} redefines {target.id}")
    assert violations == [], f"Local constant redefinitions found:\n" + "\n".join(violations)
