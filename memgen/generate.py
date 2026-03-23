#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from .wrapper import (
    DEFAULT_MODULE,
    WrapperError,
    build_shell_command,
    config_flags,
    ensure_paths,
    parse_memory_name,
    resolve_model_verilog_path,
    run_generation,
)
from .plan import build_wrapper_plan
from .uhdl_emit import emit_wrapper_artifacts


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_KITS: list[str] = []


def _write_generation_inputs(run_dir: Path, family, spec, module_name: str, kits: list[str], top_wrapper_name: str, width: int, depth: int) -> None:
    config_line = " ".join([spec.base_config, *config_flags(family, spec)]).strip()
    shell_command = build_shell_command(family, Path("config.txt"), module_name, kits)

    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "config.txt").write_text(config_line + "\n", encoding="utf-8")
    (run_dir / "run.sh").write_text(shell_command + "\n", encoding="utf-8")
    (run_dir / "request.json").write_text(
        json.dumps(
            {
                "memory_name": spec.raw_name,
                "family": family.family_id,
                "compiler_version": spec.compiler_version,
                "config_line": config_line,
                "module": module_name,
                "kits": kits,
                "wrapper_request": {
                    "top_wrapper_name": top_wrapper_name,
                    "exposed_width": width,
                    "exposed_depth": depth,
                },
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def _generate_memory_model(memory_name: str, family_override: str | None, module_name: str, kits: list[str], compiler_run_dir: Path, top_wrapper_name: str, width: int, depth: int):
    family, spec = parse_memory_name(memory_name, family_override)
    ensure_paths(family)
    _write_generation_inputs(compiler_run_dir, family, spec, module_name, kits, top_wrapper_name, width, depth)

    result = run_generation(compiler_run_dir)
    wrapper_log = compiler_run_dir / "wrapper.log"
    wrapper_log.write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode != 0:
        tail = "\n".join((result.stdout + result.stderr).splitlines()[-40:])
        detail = f"compiler 执行失败，退出码 {result.returncode}，日志: {wrapper_log}"
        if tail:
            detail += f"\n--- log tail ---\n{tail}"
        raise RuntimeError(detail)

    model_verilog = resolve_model_verilog_path(compiler_run_dir, spec)
    if not model_verilog.is_file():
        raise FileNotFoundError(f"compiler 已完成，但未找到输出 model: {model_verilog}")
    return family, spec, model_verilog, wrapper_log


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="先调用 memory compiler 生成 model，再生成 UHDL wrapper。")
    parser.add_argument("memory_name", help="TSMC memory 名称，例如 ts5n12ffcllulvta8x16m1swsho")
    parser.add_argument("top_wrapper_name", help="生成的顶层 wrapper 模块名")
    parser.add_argument("width", type=int, help="对外暴露的数据位宽")
    parser.add_argument("depth", type=int, help="对外暴露的深度")
    parser.add_argument(
        "--family",
        choices=["1prf", "2prf", "spsram", "dpsram", "uhd1prf", "uhd2prf"],
        default=None,
        help="显式指定 family；默认按 memory 名称自动识别。",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="输出目录；默认写到 mem_gen_product/<top_wrapper_name>/",
    )
    parser.add_argument(
        "--module",
        default=DEFAULT_MODULE,
        help=f"module load 使用的模块名，默认 {DEFAULT_MODULE}",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.width <= 0 or args.depth <= 0:
        print("[ERROR] width/depth 必须为正整数。", file=sys.stderr)
        return 2

    try:
        output_dir = args.output_dir or (REPO_ROOT / "mem_gen_product" / args.top_wrapper_name)
        if output_dir.exists():
            shutil.rmtree(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        compiler_run_dir = output_dir / "_compiler_run"
        family, spec, model_verilog, wrapper_log = _generate_memory_model(
            memory_name=args.memory_name,
            family_override=args.family,
            module_name=args.module,
            kits=DEFAULT_KITS,
            compiler_run_dir=compiler_run_dir,
            top_wrapper_name=args.top_wrapper_name,
            width=args.width,
            depth=args.depth,
        )
        plan = build_wrapper_plan(
            family_id=family.family_id,
            canonical_name=spec.canonical_name,
            compiler_version=spec.compiler_version,
            child_words=spec.words,
            child_bits=spec.bits,
            exposed_width=args.width,
            exposed_depth=args.depth,
            top_module_name=args.top_wrapper_name,
        )
        metadata = emit_wrapper_artifacts(output_dir, plan, model_verilog)
    except (WrapperError, FileNotFoundError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"[ERROR] wrapper 生成失败: {exc}", file=sys.stderr)
        return 3

    print(f"family            : {family.family_id}")
    print(f"memory_name       : {spec.raw_name}")
    print(f"model_verilog     : {model_verilog}")
    print(f"wrapper_log       : {wrapper_log}")
    print(f"top_wrapper_name  : {plan.top_module_name}")
    print(f"output_dir        : {output_dir}")
    print(f"top_wrapper_file  : {metadata['top_wrapper_file']}")
    print(f"child_wrapper_file: {metadata['child_wrapper_file']}")
    print(f"mapping_file      : {metadata['mapping_file']}")
    print("[OK] UHDL wrapper 生成完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
