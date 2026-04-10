#!/usr/bin/env python3
"""Batch runner for Voxelator FBX processing.

Recursively discovers FBX files under --input-dir and invokes run_voxelator_fbx.py
for each one.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


def _discover_fbx(input_dir: Path) -> list[Path]:
    files = [p for p in input_dir.rglob("*") if p.is_file() and p.suffix.lower() == ".fbx"]
    return sorted(files)


def _report_paths(input_dir: Path, report_path_arg: str) -> tuple[Path, Path]:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if report_path_arg:
        base = Path(report_path_arg).expanduser().resolve()
        if base.suffix:
            txt_path = base
            json_path = base.with_suffix(".json")
        else:
            txt_path = base / f"voxelator_batch_report_{stamp}.txt"
            json_path = base / f"voxelator_batch_report_{stamp}.json"
    else:
        txt_path = input_dir / f"voxelator_batch_report_{stamp}.txt"
        json_path = input_dir / f"voxelator_batch_report_{stamp}.json"
    return txt_path, json_path


def _extract_failure_reason(log_path: Path) -> str:
    if not log_path.exists():
        return "no batch log created"

    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as exc:
        return f"failed to read log: {exc}"

    high_priority = (
        "ModuleNotFoundError:",
        "RuntimeError: Error: Python:",
        "ERROR: FBX not found",
        "ERROR: No mesh objects found",
        "ERROR: action not found",
        "ERROR: --action All requested",
        "ERROR: Voxelator failed",
        "Aborted:",
    )
    for line in lines:
        if any(k in line for k in high_priority):
            return line.strip()

    medium_priority = (
        "RuntimeError:",
        "Traceback",
    )
    for line in lines:
        if any(k in line for k in medium_priority):
            if "unregister_class(...):, missing bl_rna" in line:
                continue
            return line.strip()

    for line in reversed(lines[-120:]):
        if "unregister_class(...):, missing bl_rna" in line:
            return "warning_only: addon unregister bl_rna message"
    return "see batch log"


def _parse_runner_result(log_path: Path) -> dict:
    default = {"found": False, "success": False, "exported": 0, "failed": 0, "error": ""}
    if not log_path.exists():
        return default

    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return default

    marker = "VOXELATOR_RESULT "
    for line in reversed(lines):
        if line.startswith(marker):
            raw = line[len(marker) :].strip()
            try:
                payload = json.loads(raw)
            except Exception:
                return default
            return {
                "found": True,
                "success": bool(payload.get("success", False)),
                "exported": int(payload.get("exported", 0)),
                "failed": int(payload.get("failed", 0)),
                "error": str(payload.get("error", "")),
            }
    return default


def _write_reports(report_txt: Path, report_json: Path, payload: dict) -> None:
    report_txt.parent.mkdir(parents=True, exist_ok=True)
    report_json.parent.mkdir(parents=True, exist_ok=True)

    lines = []
    lines.append("Voxelator Batch Report")
    lines.append(f"Started: {payload['started_at']}")
    lines.append(f"Finished: {payload['finished_at']}")
    lines.append(f"Elapsed seconds: {payload['elapsed_seconds']:.2f}")
    lines.append(f"Input dir: {payload['input_dir']}")
    lines.append(f"Runner: {payload['runner']}")
    lines.append(f"Blender: {payload['blender']}")
    lines.append(f"Total discovered: {payload['total_discovered']}")
    lines.append(f"Processed: {payload['processed']}")
    lines.append(f"Succeeded: {payload['succeeded']}")
    lines.append(f"Failed: {payload['failed']}")
    lines.append(f"Skipped: {payload['skipped']}")
    lines.append(f"Cleaned files: {payload.get('cleaned_files', 0)}")
    lines.append("")

    if payload["failures"]:
        lines.append("Failed files:")
        for item in payload["failures"]:
            lines.append(f"- FBX: {item['fbx']}")
            lines.append(f"  Return code: {item['return_code']}")
            lines.append(f"  Log: {item['log']}")
            lines.append(f"  Classification: {item['classification']}")
            lines.append(f"  Primary reason: {item['primary_reason']}")
            if item.get("secondary_reason"):
                lines.append(f"  Secondary reason: {item['secondary_reason']}")
    else:
        lines.append("Failed files: none")

    report_txt.write_text("\n".join(lines) + "\n", encoding="utf-8")
    report_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _clean_generated_outputs(fbx: Path) -> list[Path]:
    stem = fbx.stem
    out_base = f"{stem}_all"
    parent = fbx.parent

    matches = []
    matches.extend(parent.glob(f"{out_base}__*.png"))
    matches.append(parent / f"{out_base}.log")
    matches.append(parent / f"{out_base}.batch.log")

    removed = []
    seen = set()
    for path in matches:
        if path in seen:
            continue
        seen.add(path)
        if path.exists() and path.is_file():
            try:
                path.unlink()
                removed.append(path)
            except Exception:
                pass
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Voxelator recursively on all FBX files")
    parser.add_argument("--input-dir", required=True, help="Root directory to scan recursively for FBX files")
    parser.add_argument("--blender", default="blender", help="Blender executable path")
    parser.add_argument("--runner", default="", help="Path to run_voxelator_fbx.py (default: sibling file)")
    parser.add_argument("--res", type=int, default=64, help="Voxel resolution (default: 64)")
    parser.add_argument("--fill", type=int, choices=(0, 1), default=0, help="Fill volume (default: 0)")
    parser.add_argument("--separate", type=int, choices=(0, 1), default=0, help="Separate cubes (default: 0)")
    parser.add_argument("--rot-offset", type=float, default=0.0, help="Z rotation offset in degrees (default: 0)")
    parser.add_argument("--action", default="All", help="Action name or All (default: All)")
    parser.add_argument("--frame-step", type=int, default=1, help="Animation frame step (default: 1)")
    parser.add_argument("--skip-existing", action="store_true", help="Skip files with existing output pattern")
    parser.add_argument("--max-files", type=int, default=0, help="Optional cap for number of FBX files")
    parser.add_argument("--dry-run", action="store_true", help="Only list discovered files and exit")
    parser.add_argument("--clean-output", action="store_true", help="Remove existing generated outputs before processing each FBX")
    parser.add_argument("--report-path", default="", help="Report file or directory path")
    parser.add_argument(
        "--python-site",
        default=str(Path.home() / ".local" / "lib" / "python3.14" / "site-packages"),
        help="Extra site-packages path prepended to PYTHONPATH",
    )
    args = parser.parse_args()

    input_dir = Path(args.input_dir).expanduser().resolve()
    if not input_dir.is_dir():
        print(f"ERROR: input directory not found: {input_dir}")
        return 2

    if args.runner:
        runner = Path(args.runner).expanduser().resolve()
    else:
        runner = Path(__file__).with_name("run_voxelator_fbx.py").resolve()

    if not runner.is_file():
        print(f"ERROR: runner script not found: {runner}")
        return 3

    fbx_files = _discover_fbx(input_dir)
    if args.max_files > 0:
        fbx_files = fbx_files[: args.max_files]

    print(f"Discovered FBX files: {len(fbx_files)}")
    if args.dry_run:
        for f in fbx_files:
            print(f)
        return 0

    report_txt, report_json = _report_paths(input_dir, args.report_path)

    env = os.environ.copy()
    site_path = Path(args.python_site).expanduser()
    if site_path.exists():
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{site_path}:{existing}" if existing else str(site_path)

    started_at = datetime.now().isoformat(timespec="seconds")
    t_batch = time.perf_counter()

    processed = 0
    succeeded = 0
    failed = 0
    skipped = 0
    failures = []
    cleaned_files = 0

    for idx, fbx in enumerate(fbx_files, start=1):
        rel = fbx.relative_to(input_dir)

        if args.clean_output:
            removed = _clean_generated_outputs(fbx)
            cleaned_files += len(removed)
            if removed:
                print(f"[{idx}/{len(fbx_files)}] CLEAN {rel} removed={len(removed)}", flush=True)

        out_base = f"{fbx.stem}_all"
        action_pattern = f"{out_base}__*.png"
        existing_outputs = sorted(fbx.parent.glob(action_pattern))

        if args.skip_existing and existing_outputs:
            skipped += 1
            print(f"[{idx}/{len(fbx_files)}] SKIP {rel} existing={len(existing_outputs)}", flush=True)
            continue

        processed += 1
        run_log = fbx.parent / f"{out_base}.batch.log"
        cmd = [
            args.blender,
            "-b",
            "-P",
            str(runner),
            "--",
            "--fbx",
            str(fbx),
            "--out",
            f"{out_base}.png",
            "--res",
            str(max(1, args.res)),
            "--fill",
            str(args.fill),
            "--separate",
            str(args.separate),
            "--rot-offset",
            str(args.rot_offset),
            "--export-animation",
            "1",
            "--action",
            str(args.action),
            "--frame-step",
            str(max(1, args.frame_step)),
        ]

        print(f"[{idx}/{len(fbx_files)}] START {rel}", flush=True)
        t0 = time.perf_counter()
        with open(run_log, "w", encoding="utf-8") as lf:
            proc = subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT, env=env)
        dt = time.perf_counter() - t0

        generated = sorted(fbx.parent.glob(action_pattern))
        runner_result = _parse_runner_result(run_log)
        exported = len(generated)
        if runner_result["found"]:
            exported = max(exported, runner_result["exported"])

        if proc.returncode == 0 and exported > 0 and (not runner_result["found"] or runner_result["success"]):
            succeeded += 1
            print(f"[{idx}/{len(fbx_files)}] OK {rel} files={exported} in {dt:.1f}s", flush=True)
        else:
            failed += 1
            primary = _extract_failure_reason(run_log)
            secondary = ""
            classification = "unknown"
            if proc.returncode != 0:
                classification = "process_error"
            elif runner_result["found"] and not runner_result["success"]:
                classification = "runner_reported_failure"
                if runner_result["error"]:
                    secondary = f"runner_error:{runner_result['error']}"
            elif exported == 0:
                classification = "no_outputs"
                secondary = "no output files produced"
            elif "warning_only:" in primary:
                classification = "warning_only"

            failures.append(
                {
                    "fbx": str(fbx),
                    "return_code": proc.returncode,
                    "log": str(run_log),
                    "classification": classification,
                    "primary_reason": primary,
                    "secondary_reason": secondary,
                }
            )
            print(f"[{idx}/{len(fbx_files)}] FAIL {rel} rc={proc.returncode} in {dt:.1f}s", flush=True)

    elapsed = time.perf_counter() - t_batch
    finished_at = datetime.now().isoformat(timespec="seconds")

    payload = {
        "started_at": started_at,
        "finished_at": finished_at,
        "elapsed_seconds": elapsed,
        "input_dir": str(input_dir),
        "runner": str(runner),
        "blender": args.blender,
        "total_discovered": len(fbx_files),
        "processed": processed,
        "succeeded": succeeded,
        "failed": failed,
        "skipped": skipped,
        "cleaned_files": cleaned_files,
        "settings": {
            "res": args.res,
            "fill": args.fill,
            "separate": args.separate,
            "rot_offset": args.rot_offset,
            "action": args.action,
            "frame_step": args.frame_step,
            "skip_existing": args.skip_existing,
            "clean_output": args.clean_output,
        },
        "failures": failures,
    }

    _write_reports(report_txt, report_json, payload)

    print("Batch complete.")
    print(f"  Discovered: {len(fbx_files)}")
    print(f"  Processed:  {processed}")
    print(f"  Succeeded:  {succeeded}")
    print(f"  Failed:     {failed}")
    print(f"  Skipped:    {skipped}")
    print(f"  Cleaned:    {cleaned_files}")
    print(f"  Report TXT: {report_txt}")
    print(f"  Report JSON:{report_json}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
