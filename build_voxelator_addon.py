#!/usr/bin/env python3
"""Build and optionally install the Voxelator Blender add-on package."""

from __future__ import annotations

import argparse
import ast
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


PACKAGE_NAME = "Voxelator"
SOURCE_FILES = ("voxelator.py", "voxelize_native.c", "preview_voxel_slices.py")


def _repo_root() -> Path:
    return Path(__file__).resolve().parent


def _load_bl_info(voxelator_py: Path) -> dict:
    tree = ast.parse(voxelator_py.read_text(encoding="utf-8"), filename=str(voxelator_py))
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id == "bl_info":
                return ast.literal_eval(node.value)
    raise RuntimeError(f"bl_info not found in {voxelator_py}")


def _write_init(package_dir: Path, bl_info: dict) -> None:
    init_text = f"""bl_info = {bl_info!r}

from importlib import reload

from . import voxelator

reload(voxelator)

register = voxelator.register
unregister = voxelator.unregister
"""
    (package_dir / "__init__.py").write_text(init_text, encoding="utf-8")


def _copy_sources(root: Path, package_dir: Path) -> None:
    for filename in SOURCE_FILES:
        src = root / filename
        if not src.is_file():
            raise FileNotFoundError(src)
        shutil.copy2(src, package_dir / filename)


def build_package(root: Path) -> tuple[Path, Path]:
    out_root = root / "VoxelatorAddon"
    package_dir = out_root / PACKAGE_NAME
    zip_path = out_root / f"{PACKAGE_NAME}.zip"

    if package_dir.exists():
        shutil.rmtree(package_dir)
    package_dir.mkdir(parents=True, exist_ok=True)

    _copy_sources(root, package_dir)
    _write_init(package_dir, _load_bl_info(root / "voxelator.py"))

    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(package_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(out_root))

    return package_dir, zip_path


def default_addons_dir(version: str) -> Path:
    return Path.home() / ".config" / "blender" / version / "scripts" / "addons"


def install_package(package_dir: Path, addons_dir: Path, *, keep_native_lib: bool) -> Path:
    target = addons_dir / PACKAGE_NAME
    addons_dir.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(package_dir, target)

    native_lib = target / "libvoxelize.so"
    if native_lib.exists() and not keep_native_lib:
        native_lib.unlink()
    return target


def verify_with_blender(blender: str, addons_dir: Path) -> None:
    expr = f"""
import os, sys
addons_dir = {str(addons_dir)!r}
sys.path.insert(0, addons_dir)
import Voxelator
required = ['__init__.py', 'voxelator.py', 'voxelize_native.c', 'preview_voxel_slices.py']
base = os.path.join(addons_dir, 'Voxelator')
missing = [name for name in required if not os.path.isfile(os.path.join(base, name))]
print('VOXELATOR_VERIFY_NAME', Voxelator.bl_info.get('name'))
print('VOXELATOR_VERIFY_MISSING', missing)
if missing or Voxelator.bl_info.get('name') != 'Voxelator':
    raise SystemExit(1)
"""
    subprocess.run([blender, "-b", "--factory-startup", "--python-expr", expr], check=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Voxelator Blender add-on package")
    parser.add_argument("--install", action="store_true", help="Copy package into Blender add-ons folder after building")
    parser.add_argument("--blender-version", default="5.1", help="Blender config version for default install path (default: 5.1)")
    parser.add_argument("--addons-dir", default="", help="Override Blender scripts/addons directory")
    parser.add_argument("--keep-native-lib", action="store_true", help="Keep installed libvoxelize.so instead of forcing first-run rebuild")
    parser.add_argument("--verify", action="store_true", help="Verify installed package with Blender after --install")
    parser.add_argument("--blender", default="blender", help="Blender executable for --verify (default: blender)")
    args = parser.parse_args(argv)

    root = _repo_root()
    package_dir, zip_path = build_package(root)
    print(f"Built package: {package_dir}")
    print(f"Built zip:     {zip_path}")

    if args.install:
        addons_dir = Path(args.addons_dir).expanduser().resolve() if args.addons_dir else default_addons_dir(args.blender_version)
        target = install_package(package_dir, addons_dir, keep_native_lib=args.keep_native_lib)
        print(f"Installed:     {target}")
        if args.verify:
            verify_with_blender(args.blender, addons_dir)
            print("Verified with Blender")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
