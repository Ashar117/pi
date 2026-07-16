"""testing/test_swallowed_ratchet.py — T-286: swallowed-exception debt ceiling."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts import verify


def _run(root: Path, monkeypatch, baseline: int):
    baselines_file = root / "scripts_verify_baselines.json"
    baselines_file.write_text(json.dumps({"swallowed_pass": baseline}), encoding="utf-8")
    monkeypatch.setattr(verify, "ROOT", root)
    monkeypatch.setattr(verify, "BASELINES_FILE", baselines_file)
    return verify.check_swallowed_exceptions(), baselines_file


def test_new_offender_over_baseline_fails(tmp_path, monkeypatch):
    (tmp_path / "bad.py").write_text(
        "def f():\n    try:\n        1/0\n    except ValueError:\n        pass\n",
        encoding="utf-8",
    )
    (offenders, count, baseline), _ = _run(tmp_path, monkeypatch, baseline=0)
    assert offenders == ["bad.py:4"]
    assert count == 1 and baseline == 0


def test_under_baseline_lowers_it(tmp_path, monkeypatch):
    (tmp_path / "one.py").write_text(
        "def f():\n    try:\n        1/0\n    except ValueError:\n        pass\n",
        encoding="utf-8",
    )
    (offenders, count, baseline), bfile = _run(tmp_path, monkeypatch, baseline=5)
    assert offenders == []  # under baseline, not reported as new
    assert count == 1
    assert json.loads(bfile.read_text(encoding="utf-8"))["swallowed_pass"] == 1


def test_instrumented_handler_is_not_a_match(tmp_path, monkeypatch):
    """A handler that calls track_silent has a non-Pass body — never counted."""
    (tmp_path / "ok.py").write_text(
        "def f():\n    try:\n        1/0\n    except ValueError as e:\n        track_silent('x', e)\n",
        encoding="utf-8",
    )
    (offenders, count, _baseline), _ = _run(tmp_path, monkeypatch, baseline=0)
    assert offenders == [] and count == 0
