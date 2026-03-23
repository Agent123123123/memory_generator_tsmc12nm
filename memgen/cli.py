#!/usr/bin/env python3
"""memgen — TSMC 12nm Memory Generator CLI

Top-level entry point with subcommands:

  families    List all supported memory families
  check       Parse and validate a memory name (no compiler invoked)
  plan        Preview tiling plan for a given width/depth (no compiler invoked)
  generate    Invoke TSMC memory compiler to produce macro files
  run         Full pipeline: compiler invocation + UHDL RTL wrapper generation
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Lazy import helpers (keep startup fast)
# ---------------------------------------------------------------------------

def _import_wrapper():
    from memgen import wrapper
    return wrapper


def _import_plan():
    from memgen.plan import build_wrapper_plan, describe_wrapper_plan
    return build_wrapper_plan, describe_wrapper_plan


# ---------------------------------------------------------------------------
# Subcommand: families
# ---------------------------------------------------------------------------

def cmd_families(args: argparse.Namespace) -> int:
    """List all supported memory families."""
    w = _import_wrapper()
    families = w.FAMILIES

    if args.json:
        output = {
            fid: {
                "description": spec.description,
                "compiler_version": spec.compiler_version,
                "bitcell": spec.bitcell,
                "has_segment": spec.has_segment,
                "supported_tokens": sorted(spec.supported_tokens),
            }
            for fid, spec in sorted(families.items())
        }
        print(json.dumps(output, indent=2, ensure_ascii=False))
        return 0

    print(f"{'Family':<14}  {'Description':<48}  {'Compiler Version'}")
    print("-" * 80)
    for fid, spec in sorted(families.items()):
        print(f"{fid:<14}  {spec.description:<48}  {spec.compiler_version}")
    return 0


def _build_families_parser(sub):
    p = sub.add_parser(
        "families",
        help="List all supported memory families.",
        description=(
            "Print a table of all memory families supported by this wrapper, "
            "including their compiler version and bitcell type."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  memgen families\n"
            "  memgen families --json\n"
        ),
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Output in JSON format instead of a human-readable table.",
    )
    p.set_defaults(func=cmd_families)


# ---------------------------------------------------------------------------
# Subcommand: check
# ---------------------------------------------------------------------------

def cmd_check(args: argparse.Namespace) -> int:
    """Parse and validate a memory name without running the compiler."""
    w = _import_wrapper()
    try:
        family, spec = w.parse_memory_name(args.memory_name, args.family)
    except w.WrapperError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1

    if args.json:
        d = {
            "raw_name": spec.raw_name,
            "canonical_name": spec.canonical_name,
            "family": spec.family,
            "compiler_version": spec.compiler_version,
            "vt": spec.vt,
            "bitcell": spec.bitcell,
            "words": spec.words,
            "bits": spec.bits,
            "mux": spec.mux,
            "segment": spec.segment,
            "options": spec.options,
            "base_config": spec.base_config,
            "output_name": spec.output_name,
        }
        print(json.dumps(d, indent=2, ensure_ascii=False))
        return 0

    print(f"  Memory name   : {spec.raw_name}")
    print(f"  Canonical     : {spec.canonical_name}")
    print(f"  Family        : {spec.family}")
    print(f"  Compiler ver  : {spec.compiler_version}")
    print(f"  VT            : {spec.vt}")
    print(f"  Bitcell       : {spec.bitcell}")
    print(f"  Words × Bits  : {spec.words} × {spec.bits}")
    print(f"  MUX           : {spec.mux}")
    print(f"  Segment       : {spec.segment or '(none)'}")
    print(f"  Options       : {', '.join(spec.options) or '(none)'}")
    print(f"  Base config   : {spec.base_config}")
    print(f"  Output name   : {spec.output_name}")
    print("[OK] Memory name is valid.")
    return 0


def _build_check_parser(sub):
    p = sub.add_parser(
        "check",
        help="Parse and validate a memory name (no compiler invoked).",
        description=(
            "Parse a TSMC-convention memory name, resolve its family, and print "
            "all decoded parameters. No files are written and no compiler is run.\n\n"
            "Useful to verify that a name is well-formed before running 'generate'."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  memgen check ts5n12ffcllulvta8x16m1swsho\n"
            "  memgen check ts6n12ffcllulvta8x12m1fwsho --family 2prf\n"
            "  memgen check ts5n12ffcllulvta8x16m1swsho --json\n"
        ),
    )
    p.add_argument("memory_name", help="TSMC-convention memory name, e.g. ts5n12ffcllulvta8x16m1swsho")
    p.add_argument(
        "--family",
        default=None,
        help="Override family detection. If not given, family is inferred from the name prefix.",
    )
    p.add_argument("--json", action="store_true", help="Output in JSON format.")
    p.set_defaults(func=cmd_check)


# ---------------------------------------------------------------------------
# Subcommand: plan
# ---------------------------------------------------------------------------

def cmd_plan(args: argparse.Namespace) -> int:
    """Preview tiling plan for a given width/depth without running the compiler."""
    w = _import_wrapper()
    build_wrapper_plan, describe_wrapper_plan = _import_plan()

    try:
        family, spec = w.parse_memory_name(args.memory_name, args.family)
    except w.WrapperError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1

    try:
        plan = build_wrapper_plan(
            family_id=spec.family,
            canonical_name=spec.canonical_name,
            compiler_version=spec.compiler_version,
            child_words=spec.words,
            child_bits=spec.bits,
            exposed_width=args.width,
            exposed_depth=args.depth,
            top_module_name=args.top_module,
        )
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1

    if args.json:
        from memgen.plan import plan_as_dict
        print(json.dumps(plan_as_dict(plan), indent=2, ensure_ascii=False))
        return 0

    total_tiles = plan.horizontal_tiles * plan.vertical_tiles
    print(f"  Memory macro  : {plan.macro_module_name}  ({spec.words} words × {spec.bits} bits)")
    print(f"  Request       : width={args.width}, depth={args.depth}")
    print(f"  Interface     : {plan.interface_class}")
    print(f"  Tiling layout : {plan.horizontal_tiles} col(s) × {plan.vertical_tiles} row(s)  →  {total_tiles} macro(s) total")
    print(f"  Padded size   : width={plan.padded_width}, depth={plan.padded_depth}")
    print(f"  Address bits  : child={plan.child_addr_bits}, exposed={plan.exposed_addr_bits}")
    if plan.vertical_tiles > 1:
        print(f"  Row-sel bits  : {plan.row_sel_bits}")
    print(f"  Read latency  : {plan.read_latency_cycles} cycle(s)")
    print(f"  Child wrapper : {plan.child_wrapper_filename}")
    print(f"  Top wrapper   : {plan.top_wrapper_filename}")
    print()
    print(f"  {'Instance':<20}  {'Row':<4}  {'Col':<4}  {'Data bits':<14}  {'Addr range'}")
    print("  " + "-" * 70)
    for tile in plan.tiles:
        data_range = f"[{tile.data_bit_high}:{tile.data_bit_low}]"
        addr_range = f"{tile.depth_start}..{tile.valid_depth_end}"
        pad_note = f"  (+{tile.padded_data_bits}b pad)" if tile.padded_data_bits else ""
        print(f"  {tile.instance_name:<20}  {tile.row:<4}  {tile.col:<4}  {data_range:<14}  {addr_range}{pad_note}")
    return 0


def _build_plan_parser(sub):
    p = sub.add_parser(
        "plan",
        help="Preview the tiling plan for a given width/depth without running the compiler.",
        description=(
            "Calculate how many macro tiles are needed to implement the requested "
            "logical width × depth, and show each tile's data-bit and address mapping.\n\n"
            "No files are written, no compiler is invoked."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  memgen plan ts5n12ffcllulvta8x16m1swsho --width 40 --depth 20\n"
            "  memgen plan ts5n12ffcllulvta8x16m1swsho --width 128 --depth 512 --json\n"
            "  memgen plan ts6n12ffcllulvta8x12m1fwsho --width 64 --depth 64 --top-module my_rf\n"
        ),
    )
    p.add_argument("memory_name", help="TSMC-convention memory name")
    p.add_argument("--width",  type=int, required=True, help="Desired logical data width (bits)")
    p.add_argument("--depth",  type=int, required=True, help="Desired logical depth (number of words)")
    p.add_argument("--family", default=None, help="Override family detection")
    p.add_argument("--top-module", default=None, dest="top_module", help="Override generated top module name")
    p.add_argument("--json", action="store_true", help="Output full plan in JSON format")
    p.set_defaults(func=cmd_plan)


# ---------------------------------------------------------------------------
# Subcommand: generate
# ---------------------------------------------------------------------------

def cmd_generate(args: argparse.Namespace) -> int:
    """Invoke TSMC memory compiler to produce macro files."""
    w = _import_wrapper()

    # Reconstruct a Namespace compatible with memory_wrapper.main()
    sys.argv = ["memgen-generate"]  # prevent parse_args() from reading CLI
    ns = argparse.Namespace(
        memory_name=args.memory_name,
        family=args.family,
        list_families=False,
        workdir=args.workdir,
        run_dir=args.run_dir,
        module=args.module,
        kits=args.kits or [],
        prepare_only=args.prepare_only,
        force=args.force,
        wrapper_width=args.wrapper_width,
        wrapper_depth=args.wrapper_depth,
    )

    try:
        family, spec = w.parse_memory_name(args.memory_name, args.family)
        w.ensure_paths(family)
        w.validate_wrapper_args(ns)
    except w.WrapperError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1

    run_dir = args.run_dir or (args.workdir / args.memory_name)
    run_dir = Path(run_dir)

    if run_dir.exists() and not args.force:
        print(f"[ERROR] Run directory already exists: {run_dir}\n        Use --force to overwrite.", file=sys.stderr)
        return 1

    config_line = " ".join([spec.base_config, *w.config_flags(family, spec)]).strip()
    shell_cmd = w.build_shell_command(family, run_dir / "config.txt", args.module, args.kits or [])
    w.write_run_artifacts(run_dir, family, spec, config_line, shell_cmd, ns,
                          wrapper_metadata=None)
    print(f"[INFO] Artifacts written to: {run_dir}")

    if args.prepare_only:
        print("[INFO] --prepare-only: skipping compiler invocation.")
        return 0

    result = w.run_generation(run_dir)
    log_path = run_dir / "wrapper.log"
    log_path.write_text(result.stdout + result.stderr, encoding="utf-8")

    if result.returncode != 0:
        tail = "\n".join((result.stdout + result.stderr).splitlines()[-40:])
        print(f"[ERROR] Compiler failed (exit {result.returncode}). Log: {log_path}", file=sys.stderr)
        if tail:
            print(tail, file=sys.stderr)
        return result.returncode

    print(f"[OK] Compiler finished. Log: {log_path}")

    if args.wrapper_width and args.wrapper_depth:
        from memgen.plan import build_wrapper_plan
        from memgen.uhdl_emit import emit_wrapper_artifacts
        model_v = w.resolve_model_verilog_path(run_dir, spec)
        plan = build_wrapper_plan(
            family_id=spec.family,
            canonical_name=spec.canonical_name,
            compiler_version=spec.compiler_version,
            child_words=spec.words,
            child_bits=spec.bits,
            exposed_width=args.wrapper_width,
            exposed_depth=args.wrapper_depth,
        )
        out_dir = run_dir / "wrapper_rtl"
        emit_wrapper_artifacts(plan, model_v, out_dir)
        print(f"[OK] Wrapper RTL written to: {out_dir}")

    return 0


def _build_generate_parser(sub, kit_choices):
    p = sub.add_parser(
        "generate",
        help="Invoke TSMC memory compiler to produce macro files.",
        description=(
            "Parse the memory name, create config.txt and run.sh, then invoke the "
            "TSMC memory compiler to generate the requested output kits.\n\n"
            "Optionally also generate a stitched UHDL RTL wrapper with --wrapper-width/--wrapper-depth."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  # Generate DATASHEET only (fast)\n"
            "  memgen generate ts5n12ffcllulvta8x16m1swsho --kits DATASHEET\n\n"
            "  # Generate Verilog model + LEF (for front-end + back-end)\n"
            "  memgen generate ts5n12ffcllulvta8x16m1swsho --kits VERILOG LEF\n\n"
            "  # Prepare files only, do not invoke compiler\n"
            "  memgen generate ts5n12ffcllulvta8x16m1swsho --kits VERILOG --prepare-only\n\n"
            "  # Compiler + auto RTL wrapper (40-bit wide, 20-entry deep)\n"
            "  memgen generate ts5n12ffcllulvta8x16m1swsho --kits VERILOG \\\n"
            "      --wrapper-width 40 --wrapper-depth 20\n"
        ),
    )
    p.add_argument("memory_name", help="TSMC-convention memory name, e.g. ts5n12ffcllulvta8x16m1swsho")
    p.add_argument(
        "--kits", nargs="*", choices=kit_choices, default=[],
        metavar="KIT",
        help=(
            f"Output kits to generate. Choices: {', '.join(sorted(kit_choices))}.\n"
            "Leave empty for full compiler output."
        ),
    )
    p.add_argument("--family", default=None, help="Override family detection")
    p.add_argument("--module", default=None,
                   help="module load name for the EDA environment (default: mc2_n12/2013.12)")
    p.add_argument("--workdir", type=Path, default=Path.cwd() / "runs",
                   help="Parent directory for run folders (default: ./runs/)")
    p.add_argument("--run-dir", type=Path, default=None, dest="run_dir",
                   help="Explicit run directory path; overrides --workdir")
    p.add_argument("--prepare-only", action="store_true",
                   help="Write config.txt / run.sh / request.json but do NOT invoke the compiler")
    p.add_argument("--force", action="store_true",
                   help="Overwrite run directory if it already exists")
    p.add_argument("--wrapper-width", type=int, default=None, dest="wrapper_width",
                   help="If set, also generate a stitched RTL wrapper with this logical data width")
    p.add_argument("--wrapper-depth", type=int, default=None, dest="wrapper_depth",
                   help="Logical depth for the stitched RTL wrapper (requires --wrapper-width)")
    p.set_defaults(func=cmd_generate)


# ---------------------------------------------------------------------------
# Subcommand: run  (full pipeline)
# ---------------------------------------------------------------------------

def cmd_run(args: argparse.Namespace) -> int:
    """Full pipeline: compiler invocation + UHDL RTL wrapper generation."""
    from memgen.generate import (
        _generate_memory_model,
        _write_generation_inputs,
    )
    from memgen.plan import build_wrapper_plan
    from memgen.uhdl_emit import emit_wrapper_artifacts
    w = _import_wrapper()

    module_name = args.module or w.DEFAULT_MODULE

    compiler_run_dir = args.output_dir / "_compiler_run" if args.output_dir else (
        Path.cwd() / "mem_gen_product" / args.top_wrapper_name / "_compiler_run"
    )
    output_dir = args.output_dir or (Path.cwd() / "mem_gen_product" / args.top_wrapper_name)

    print(f"[INFO] Compiler run dir : {compiler_run_dir}")
    print(f"[INFO] Output dir       : {output_dir}")

    try:
        family, spec, model_verilog, wrapper_log = _generate_memory_model(
            memory_name=args.memory_name,
            family_override=args.family,
            module_name=module_name,
            kits=[],
            compiler_run_dir=compiler_run_dir,
            top_wrapper_name=args.top_wrapper_name,
            width=args.width,
            depth=args.depth,
        )
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1

    print(f"[OK] Compiler done. Verilog model: {model_verilog}")

    plan = build_wrapper_plan(
        family_id=spec.family,
        canonical_name=spec.canonical_name,
        compiler_version=spec.compiler_version,
        child_words=spec.words,
        child_bits=spec.bits,
        exposed_width=args.width,
        exposed_depth=args.depth,
        top_module_name=args.top_wrapper_name,
    )

    rtl_out = output_dir / "wrapper_rtl"
    emit_wrapper_artifacts(plan, model_verilog, rtl_out)
    print(f"[OK] Wrapper RTL written to: {rtl_out}")
    return 0


def _build_run_parser(sub):
    p = sub.add_parser(
        "run",
        help="Full pipeline: TSMC compiler invocation + UHDL RTL wrapper generation.",
        description=(
            "Complete end-to-end flow:\n"
            "  1. Invoke TSMC memory compiler to produce the macro Verilog model\n"
            "  2. Compute tiling plan for the requested width × depth\n"
            "  3. Generate tile wrapper + top stitching wrapper using UHDL\n\n"
            "The compiler is always re-invoked (no caching of previous models).\n"
            "Intermediate compiler outputs are kept in <output-dir>/_compiler_run/."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  memgen run ts5n12ffcllulvta8x16m1swsho my_rf --width 40 --depth 20\n\n"
            "  memgen run ts6n12ffcllulvta8x12m1fwsho cache_mem \\\n"
            "      --width 128 --depth 512 \\\n"
            "      --output-dir ./my_project/mem_out\n"
        ),
    )
    p.add_argument("memory_name",    help="TSMC-convention memory name")
    p.add_argument("top_wrapper_name", help="Module name for the generated top-level wrapper")
    p.add_argument("--width",  type=int, required=True, help="Logical data width of the wrapper (bits)")
    p.add_argument("--depth",  type=int, required=True, help="Logical depth of the wrapper (words)")
    p.add_argument("--family", default=None, help="Override family detection")
    p.add_argument("--module", default=None,
                   help="module load name for the EDA environment (default: mc2_n12/2013.12)")
    p.add_argument("--output-dir", type=Path, default=None, dest="output_dir",
                   help="Root output directory (default: ./mem_gen_product/<top_wrapper_name>/)")
    p.set_defaults(func=cmd_run)


# ---------------------------------------------------------------------------
# Root parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    from memgen.wrapper import KIT_FLAGS, FAMILIES, DEFAULT_MODULE

    parser = argparse.ArgumentParser(
        prog="memgen",
        description=(
            "memgen — TSMC 12nm Memory Generator CLI\n\n"
            "A unified command-line interface for the TSMC 12nm memory compiler\n"
            "wrapper and automatic UHDL RTL wrapper generator.\n\n"
            "Supported memory families:\n"
            + "".join(f"  {fid:<14}  {spec.description}\n"
                      for fid, spec in sorted(FAMILIES.items()))
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Quick start:\n"
            f"  memgen families                                         # list families\n"
            f"  memgen check   ts5n12ffcllulvta8x16m1swsho              # validate name\n"
            f"  memgen plan    ts5n12ffcllulvta8x16m1swsho --width 40 --depth 20\n"
            f"  memgen generate ts5n12ffcllulvta8x16m1swsho --kits DATASHEET VERILOG\n"
            f"  memgen run     ts5n12ffcllulvta8x16m1swsho my_rf --width 40 --depth 20\n\n"
            f"Default EDA module: {DEFAULT_MODULE}\n"
            f"Run 'memgen <command> --help' for per-command usage.\n"
        ),
    )
    parser.add_argument(
        "--version", action="version",
        version="%(prog)s 0.1.0",
    )

    sub = parser.add_subparsers(
        title="commands",
        dest="command",
        metavar="<command>",
    )
    sub.required = True

    kit_choices = sorted(KIT_FLAGS.keys())
    _build_families_parser(sub)
    _build_check_parser(sub)
    _build_plan_parser(sub)
    _build_generate_parser(sub, kit_choices)
    _build_run_parser(sub)

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
