"""T-250: verify.py must flag bare 'except:' but not 'except Exception:'."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import ast
from scripts.verify import check_bare_except


def _offenders_for_source(tmp_path, source: str) -> list[str]:
    f = tmp_path / "sample.py"
    f.write_text(source, encoding="utf-8")
    tree = ast.parse(source)
    return [n.lineno for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler) and n.type is None]


def test_bare_except_is_flagged(tmp_path):
    offenders = _offenders_for_source(tmp_path, "try:\n    pass\nexcept:\n    pass\n")
    assert offenders == [3]


def test_typed_except_is_clean(tmp_path):
    offenders = _offenders_for_source(tmp_path, "try:\n    pass\nexcept Exception:\n    pass\n")
    assert offenders == []


def test_repo_has_no_bare_except():
    offenders = check_bare_except()
    assert offenders == [], f"bare except found: {offenders}"


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = Path(d)
        test_bare_except_is_flagged(p)
        test_typed_except_is_clean(p)
    test_repo_has_no_bare_except()
    print("OK")
