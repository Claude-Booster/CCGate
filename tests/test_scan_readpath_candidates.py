import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from scan_readpath_candidates import scan  # noqa: E402


def _write(p: Path, text: str = "x") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _build_repo(root: Path) -> None:
    # lockfiles: one at root, one in a workspace package (monorepo case)
    _write(root / "package-lock.json")
    _write(root / "packages" / "app" / "yarn.lock")
    # real source — must NOT be flagged
    _write(root / "src" / "index.js")
    _write(root / "packages" / "app" / "src" / "App.jsx")
    # vendored dirs: root node_modules + nested node_modules
    _write(root / "node_modules" / "lodash" / "index.js")
    _write(root / "packages" / "app" / "node_modules" / "react" / "index.js")
    # build dir with a .min.js INSIDE it (should be covered by the dir, not counted separately)
    _write(root / "dist" / "bundle.js")
    _write(root / "dist" / "bundle.min.js")
    # a .min.js OUTSIDE any flagged dir (this one IS a min-asset finding)
    _write(root / "public" / "lib.min.js")


def test_scan_detects_each_finding_type(tmp_path):
    _build_repo(tmp_path)
    f = scan(tmp_path, with_sizes=False)

    assert len(f["lockfile"]["paths"]) == 2
    assert len(f["vendored-dir"]["paths"]) == 2   # root + nested node_modules
    assert len(f["build-dir"]["paths"]) == 1      # dist


def test_min_asset_inside_build_dir_is_pruned_not_double_counted(tmp_path):
    _build_repo(tmp_path)
    f = scan(tmp_path, with_sizes=False)
    mins = f["min-asset"]["paths"]
    # only the public/ one; the dist/bundle.min.js is under a pruned build dir
    assert len(mins) == 1
    assert mins[0].replace("\\", "/") == "public/lib.min.js"


def test_real_source_not_flagged(tmp_path):
    _build_repo(tmp_path)
    f = scan(tmp_path, with_sizes=False)
    all_paths = "\n".join(
        p for bucket in f.values() for p in bucket["paths"]
    ).replace("\\", "/")
    assert "src/index.js" not in all_paths
    assert "src/App.jsx" not in all_paths


def test_sizes_summed_when_requested(tmp_path):
    _build_repo(tmp_path)
    f = scan(tmp_path, with_sizes=True)
    # every present finding-type should report a positive byte total
    for name in ("lockfile", "vendored-dir", "build-dir", "min-asset"):
        assert f[name]["bytes"] > 0


def test_empty_repo_finds_nothing(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hi')", encoding="utf-8")
    f = scan(tmp_path, with_sizes=False)
    assert all(len(b["paths"]) == 0 for b in f.values())
