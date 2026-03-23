#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Literal

from .plan import build_wrapper_plan, describe_wrapper_plan
from .uhdl_emit import emit_wrapper_artifacts

DEFAULT_MODULE = "mc2_n12/2013.12"
ROOT_COMPILER_DIR = Path("/data/foundry/TSMC12/Memory_compiler")
OPTION_PARSE_TOKENS = ["cp", "w", "b", "y", "a", "z", "s", "h", "o", "d", "c", "r", "p", "x", "t"]
BIST_RELATED_TOKENS = {"b", "y"}
KIT_FLAGS = {
    "DATASHEET": "-DATASHEET",
    "VERILOG": "-VERILOG",
    "NLDM": "-NLDM",
    "LEF": "-LEF",
    "SPICE": "-SPICE",
    "GDSII": "-GDSII",
    "DFT": "-DFT",
    "CCS": "-CCS",
    "ECSM": "-ECSM",
    "AVM": "-AVM",
    "REDHAWK": "-REDHAWK",
}

class WrapperError(Exception):
    pass

def _full_path(*parts: str) -> Path:
    return ROOT_COMPILER_DIR.joinpath(*parts)

@dataclass(frozen=True)
class FamilySpec:
    family_id: str
    description: str
    compiler_version: str
    compiler_dir: Path
    script_name: str
    comp_no: str
    bitcell: str
    has_segment: bool
    default_tokens: frozenset[str]
    supported_tokens: frozenset[str]
    positive_flag_map: dict[str, str]
    negative_flag_map: dict[str, str]
    bist_disable_mode: Literal["config", "cli"] = "config"
    allowed_segments: frozenset[str] = frozenset({"s", "m", "f"})
    validator: Callable[["MemorySpec"], None] | None = None
    @property
    def script_path(self) -> Path:
        return self.compiler_dir / self.script_name

@dataclass
class MemorySpec:
    raw_name: str
    canonical_name: str
    version: str | None
    family: str
    compiler_version: str
    comp_no: str
    vt: str
    bitcell: str
    words: int
    bits: int
    mux: int
    segment: str
    options: list[str]
    @property
    def output_name(self) -> str:
        return f"{self.canonical_name}_{self.compiler_version}"
    @property
    def base_config(self) -> str:
        seg = self.segment if self.segment else ""
        return f"{self.words}x{self.bits}m{self.mux}{seg}"

def validate_1prf_like(spec: MemorySpec) -> None:
    mux = spec.mux
    words = spec.words
    bits = spec.bits
    if mux == 1:
        if not (8 <= words <= 128 and words % 4 == 0):
            raise WrapperError("MUX1 要求 word depth 在 8~128 且为 4 的倍数。")
        if not (16 <= bits <= 288 and bits % 2 == 0):
            raise WrapperError("MUX1 要求 bit width 在 16~288 且为 2 的倍数。")
    elif mux == 2:
        if not (16 <= words <= 256 and words % 8 == 0):
            raise WrapperError("MUX2 要求 word depth 在 16~256 且为 8 的倍数。")
        if not (8 <= bits <= 144):
            raise WrapperError("MUX2 要求 bit width 在 8~144。")
    elif mux == 4:
        if not (32 <= words <= 512 and words % 16 == 0):
            raise WrapperError("MUX4 要求 word depth 在 32~512 且为 16 的倍数。")
        if not (4 <= bits <= 72):
            raise WrapperError("MUX4 要求 bit width 在 4~72。")
    elif mux == 8:
        if not (64 <= words <= 1024 and words % 32 == 0):
            raise WrapperError("MUX8 要求 word depth 在 64~1024 且为 32 的倍数。")
        if not (4 <= bits <= 36):
            raise WrapperError("MUX8 要求 bit width 在 4~36。")
    else:
        raise WrapperError("当前 family 仅支持 mux = 1/2/4/8。")

def validate_power_options(spec: MemorySpec) -> None:
    enabled = set(spec.options)
    state = ("s" in enabled, "h" in enabled, "o" in enabled)
    dual_rail = "d" in enabled
    if dual_rail:
        valid = {(True, False, True), (False, True, True), (True, True, True)}
        if state not in valid:
            raise WrapperError("Dual rail 模式下仅支持 SOD / HOD / SHOD 组合。")
    else:
        valid = {(False, False, False), (True, False, False), (True, False, True), (False, True, True), (True, True, True)}
        if state not in valid:
            raise WrapperError("Single rail 模式下仅支持 NO / S / SO / HO / SHO 组合。")

FAMILIES: dict[str, FamilySpec] = {
    "1prf": FamilySpec("1prf", "One Port Register File", "130c", _full_path("tsn12ffcll1prf_20131200_130c", "0971001_20211221", "TSMCHOME", "sram", "Compiler", "tsn12ffcll1prf_20131200_130c"), "tsn12ffcll1prf_130c.pl", "5", "a", True, frozenset({"w", "s", "h", "o"}), frozenset({"w", "s", "h", "o", "d", "cp"}), {"d": "-DualRail", "cp": "-ColRed"}, {"w": "-NonBWEB", "s": "-NonSLP", "h": "-NonDSLP", "o": "-NonSD"}, validator=lambda spec: (validate_1prf_like(spec), validate_power_options(spec))),
    "2prf": FamilySpec("2prf", "Two Port Register File", "130a", _full_path("tsn12ffcll2prf_20131200_130a", "0971001_20211221", "TSMCHOME", "sram", "Compiler", "tsn12ffcll2prf_20131200_130a"), "tsn12ffcll2prf_130a.pl", "6", "a", True, frozenset({"w", "s", "h", "o"}), frozenset({"w", "s", "h", "o", "d", "cp"}), {"d": "-DualRail", "cp": "-ColRed"}, {"w": "-NonBWEB", "s": "-NonSLP", "h": "-NonDSLP", "o": "-NonSD"}, validator=validate_power_options),
    "spsram": FamilySpec("spsram", "Single Port SRAM", "130b", _full_path("tsn12ffcllspsram_20131200_130b", "0971001_20211221", "TSMCHOME", "sram", "Compiler", "tsn12ffcllspsram_20131200_130b"), "tsn12ffcllspsram_130b.pl", "1", "a", True, frozenset({"w", "s", "h", "o"}), frozenset({"w", "s", "h", "o", "d", "cp"}), {"d": "-DualRail", "cp": "-ColRed"}, {"w": "-NonBWEB", "s": "-NonSLP", "h": "-NonDSLP", "o": "-NonSD"}, validator=validate_power_options),
    "dpsram": FamilySpec("dpsram", "Dual Port SRAM", "130c", _full_path("tsn12ffclldpsram_20131200_130c", "0971001_20211221", "TSMCHOME", "sram", "Compiler", "tsn12ffclldpsram_20131200_130c"), "tsn12ffclldpsram_130c.pl", "d", "a", False, frozenset({"w", "s", "h", "o"}), frozenset({"w", "s", "h", "o", "d", "cp"}), {"d": "-DualRail", "cp": "-ColRed"}, {"w": "-NonBWEB", "s": "-NonSLP", "h": "-NonDSLP", "o": "-NonSD"}, validator=validate_power_options),
    "uhd1prf": FamilySpec("uhd1prf", "Ultra High Density 1PRF", "130c", _full_path("tsn12ffclluhd1prf_20131200_130c", "0971001_20211221", "TSMCHOME", "sram", "Compiler", "tsn12ffclluhd1prf_20131200_130c"), "tsn12ffclluhd1prf_130c.pl", "7", "a", True, frozenset({"w", "s", "h", "o"}), frozenset({"w", "s", "h", "o", "d", "cp"}), {"d": "-DualRail", "cp": "-ColRed"}, {"w": "-NonBWEB", "s": "-NonSLP", "h": "-NonDSLP", "o": "-NonSD"}, validator=validate_power_options),
    "uhd2prf": FamilySpec("uhd2prf", "Ultra High Density 2PRF", "130b", _full_path("tsn12ffclluhd2prf_20131200_130b", "0971001_20211221", "TSMCHOME", "sram", "Compiler", "tsn12ffclluhd2prf_20131200_130b"), "tsn12ffclluhd2prf_130b.pl", "6", "b", False, frozenset({"w", "s", "h", "o"}), frozenset({"w", "s", "h", "o", "d", "cp"}), {"d": "-DualRail", "cp": "-ColRed"}, {"w": "-NonBWEB", "s": "-NonSLP", "h": "-NonDSLP", "o": "-NonSD"}, bist_disable_mode="cli", validator=validate_power_options),
}
PREFIX_TO_FAMILY = {(family.comp_no, family.bitcell): family.family_id for family in FAMILIES.values()}

def tokenize_suffix(suffix: str) -> list[str]:
    suffix = suffix.lower()
    tokens: list[str] = []
    while suffix:
        for token in OPTION_PARSE_TOKENS:
            if suffix.startswith(token):
                tokens.append(token)
                suffix = suffix[len(token):]
                break
        else:
            raise WrapperError(f"无法识别 option 后缀片段: {suffix!r}")
    return tokens

def detect_family(memory_name: str, family_override: str | None) -> FamilySpec:
    prefix_match = re.match(r"^ts(?P<compno>[0-9d])n12ffcll(?P<vt>ulvt|lvt|svt)(?P<bitcell>[a-z])", memory_name, re.IGNORECASE)
    if not prefix_match:
        raise WrapperError("无法从名字前缀识别 TSMC convention memory name。")
    comp_no = prefix_match.group("compno").lower()
    bitcell = prefix_match.group("bitcell").lower()
    if (comp_no, bitcell) == ("3", "a"):
        raise WrapperError("ROM family 已被包装器禁用，不再支持生成。")
    auto_family = PREFIX_TO_FAMILY.get((comp_no, bitcell))
    if auto_family is None:
        raise WrapperError(f"当前还不支持 compNo={comp_no!r}, bitcell={bitcell!r} 这组前缀。")
    if family_override is None:
        return FAMILIES[auto_family]
    family = FAMILIES[family_override]
    if (family.comp_no, family.bitcell) != (comp_no, bitcell):
        raise WrapperError(f"输入名字自动识别为 {auto_family}，但你显式指定了 {family_override}；两者前缀不一致。")
    return family

def parse_memory_name(name: str, family_override: str | None = None) -> tuple[FamilySpec, MemorySpec]:
    raw_name = name.strip()
    lowered = raw_name.lower()
    family = detect_family(lowered, family_override)
    m = re.fullmatch(r"ts(?P<compno>[0-9d])n12ffcll(?P<vt>ulvt|lvt|svt)(?P<bitcell>[a-z])(?P<rest>.+?)(?:_(?P<version>[a-z0-9]+))?", lowered)
    if not m:
        raise WrapperError("无法完整解析 memory 名称。")
    rest = m.group("rest")
    version = m.group("version")
    if version and version != family.compiler_version:
        raise WrapperError(f"输入名称版本为 {version!r}，但 {family.family_id} 当前绑定的 compiler 版本是 {family.compiler_version!r}。")
    if family.has_segment:
        body = re.fullmatch(r"(?P<words>\d+)x(?P<bits>\d+)m(?P<mux>\d+)(?P<segment>[smf])(?P<opts>[a-z0-9]*)", rest)
    else:
        body = re.fullmatch(r"(?P<words>\d+)x(?P<bits>\d+)m(?P<mux>\d+)(?P<opts>[a-z0-9]*)", rest)
    if not body:
        raise WrapperError(f"{family.family_id} 的名字主体格式不合法: {rest!r}")
    words = int(body.group("words")); bits = int(body.group("bits")); mux = int(body.group("mux"))
    if words <= 0 or bits <= 0 or mux <= 0:
        raise WrapperError("words/bits/mux 必须都是正整数。")
    segment = body.groupdict().get("segment") or ""
    if segment and segment not in family.allowed_segments:
        raise WrapperError(f"{family.family_id} 不支持 segment={segment!r}。")
    options = tokenize_suffix(body.group("opts"))
    banned_tokens = [token for token in options if token in BIST_RELATED_TOKENS]
    if banned_tokens:
        raise WrapperError(f"当前包装器已永久禁用 BIST 相关特性，不允许出现这些 token: {banned_tokens}。")
    unsupported = [token for token in options if token not in family.supported_tokens]
    if unsupported:
        raise WrapperError(f"{family.family_id} 当前包装器不支持这些 option token: {unsupported}。")
    spec = MemorySpec(raw_name=raw_name, canonical_name=lowered if version is None else lowered[: -(len(version) + 1)], version=version, family=family.family_id, compiler_version=family.compiler_version, comp_no=family.comp_no, vt=m.group("vt"), bitcell=family.bitcell, words=words, bits=bits, mux=mux, segment=segment, options=options)
    if family.validator is not None:
        family.validator(spec)
    return family, spec

def config_flags(family: FamilySpec, spec: MemorySpec) -> list[str]:
    flags: list[str] = []
    enabled = set(spec.options)
    if spec.vt == "svt": flags.append("-SVT")
    elif spec.vt == "lvt": flags.append("-LVT")
    if family.bist_disable_mode == "config": flags.append("-NonBIST")
    for token in sorted(family.default_tokens):
        flag = family.negative_flag_map.get(token)
        if flag and token not in enabled: flags.append(flag)
    for token, flag in family.positive_flag_map.items():
        if token in enabled and token not in family.default_tokens: flags.append(flag)
    return flags

def build_shell_command(family: FamilySpec, config_path: Path, module_name: str, kit_names: list[str]) -> str:
    kit_flags = " ".join(KIT_FLAGS[name] for name in kit_names)
    extra_flags: list[str] = []
    if family.bist_disable_mode == "cli": extra_flags.append("-NonBIST")
    parts = [
        "set -e",
        f"module load {shlex.quote(module_name)}",
        f"export MC_HOME={shlex.quote(str(family.compiler_dir))}",
        f"perl {shlex.quote(str(family.script_path))} -file {shlex.quote(config_path.name)}" + (f" {' '.join(extra_flags)}" if extra_flags else "") + (f" {kit_flags}" if kit_flags else ""),
    ]
    return "\n".join(parts)

def ensure_paths(family: FamilySpec) -> None:
    if not family.compiler_dir.is_dir(): raise WrapperError(f"compiler 目录不存在: {family.compiler_dir}")
    if not family.script_path.is_file(): raise WrapperError(f"compiler 脚本不存在: {family.script_path}")

def write_run_artifacts(run_dir: Path, family: FamilySpec, spec: MemorySpec, config_line: str, shell_command: str, args: argparse.Namespace, wrapper_metadata: dict | None) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / 'config.txt').write_text(config_line + '\n', encoding='utf-8')
    (run_dir / 'run.sh').write_text(shell_command + '\n', encoding='utf-8')
    payload = {
        'memory_name': spec.raw_name,
        'family': family.family_id,
        'description': family.description,
        'bist_policy': 'disabled',
        'parsed': asdict(spec),
        'config_line': config_line,
        'kits': args.kits,
        'module': args.module,
        'compiler_dir': str(family.compiler_dir),
        'script': str(family.script_path),
    }
    if wrapper_metadata is not None:
        payload['wrapper_request'] = {'exposed_width': args.wrapper_width, 'exposed_depth': args.wrapper_depth, **wrapper_metadata}
    (run_dir / 'request.json').write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')

def run_generation(run_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(['bash', 'run.sh'], cwd=run_dir, text=True, capture_output=True)


def resolve_model_verilog_path(run_dir: Path, spec: MemorySpec) -> Path:
    return run_dir / spec.output_name / 'VERILOG' / f'{spec.output_name}.v'

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='根据 TSMC convention memory 名称生成 config/命令并调用多 family TSMC memory compiler。')
    parser.add_argument('memory_name', nargs='?', help='例如: ts5n12ffcllulvta8x16m1swsho')
    parser.add_argument('--family', choices=sorted(FAMILIES.keys()), default=None, help='显式指定 family；默认按名字前缀自动识别。')
    parser.add_argument('--list-families', action='store_true', help='列出当前包装器支持的 family 后退出。')
    parser.add_argument('--workdir', type=Path, default=Path.cwd() / 'runs', help='存放每次生成任务目录的父目录，默认当前目录下 runs/')
    parser.add_argument('--run-dir', type=Path, default=None, help='显式指定本次任务目录；未指定时默认使用 <workdir>/<memory_name>/')
    parser.add_argument('--module', default=DEFAULT_MODULE, help=f'module load 使用的模块名，默认 {DEFAULT_MODULE}')
    parser.add_argument('--kits', nargs='*', choices=sorted(KIT_FLAGS.keys()), default=[], help='仅生成指定 kit；留空表示跑 compiler 默认全量输出。')
    parser.add_argument('--prepare-only', action='store_true', help='只生成 config.txt / run.sh / request.json，不实际启动编译。')
    parser.add_argument('--force', action='store_true', help='若任务目录已存在则覆盖包装层文件。')
    parser.add_argument('--wrapper-width', type=int, default=None, help='启用 stitched wrapper 生成时，对外暴露的数据位宽。')
    parser.add_argument('--wrapper-depth', type=int, default=None, help='启用 stitched wrapper 生成时，对外暴露的深度。')
    return parser.parse_args()

def validate_wrapper_args(args: argparse.Namespace) -> None:
    dims = (args.wrapper_width, args.wrapper_depth)
    if dims == (None, None): return
    if args.wrapper_width is None or args.wrapper_depth is None:
        raise WrapperError('启用 wrapper 生成时必须同时提供 --wrapper-width 和 --wrapper-depth。')
    if args.wrapper_width <= 0 or args.wrapper_depth <= 0:
        raise WrapperError('--wrapper-width 和 --wrapper-depth 都必须是正整数。')
    if args.prepare_only:
        raise WrapperError('当前 UHDL wrapper 需要先拿到 compiler 生成的 memory verilog model；请去掉 --prepare-only 后再生成 wrapper。')

def print_families() -> None:
    print('Supported families:')
    for family_id in sorted(FAMILIES):
        family = FAMILIES[family_id]
        print(f"- {family_id:8s} prefix=ts{family.comp_no}...{family.bitcell} version={family.compiler_version} desc={family.description} bist=disabled")

def main() -> int:
    args = parse_args()
    if args.list_families:
        print_families(); return 0
    if not args.memory_name:
        print('[ERROR] 请提供 memory_name，或者使用 --list-families。', file=sys.stderr); return 2
    try:
        validate_wrapper_args(args)
        family, spec = parse_memory_name(args.memory_name, args.family)
        ensure_paths(family)
    except WrapperError as exc:
        print(f'[ERROR] {exc}', file=sys.stderr); return 2
    run_dir = args.run_dir or (args.workdir / spec.canonical_name)
    if run_dir.exists() and any(run_dir.iterdir()) and not args.force:
        print(f'[ERROR] 任务目录已存在且非空: {run_dir}\n如需复用该目录，请加 --force；或者换一个 --run-dir。', file=sys.stderr)
        return 2
    wrapper_plan = None
    wrapper_metadata = None
    if args.wrapper_width is not None and args.wrapper_depth is not None:
        wrapper_plan = build_wrapper_plan(family_id=family.family_id, canonical_name=spec.canonical_name, compiler_version=spec.compiler_version, child_words=spec.words, child_bits=spec.bits, exposed_width=args.wrapper_width, exposed_depth=args.wrapper_depth)
        wrapper_metadata = describe_wrapper_plan(wrapper_plan)
    config_line = ' '.join([spec.base_config, *config_flags(family, spec)]).strip()
    shell_command = build_shell_command(family, Path('config.txt'), args.module, args.kits)
    write_run_artifacts(run_dir, family, spec, config_line, shell_command, args, wrapper_metadata)
    print(f'family      : {family.family_id}')
    print(f'memory_name : {spec.raw_name}')
    print(f'config_line : {config_line}')
    print(f'run_dir     : {run_dir}')
    if wrapper_metadata is not None:
        print(f"wrapper     : {wrapper_metadata['top_module_name']} ({wrapper_metadata['interface_class']})")
    print('command     :')
    print(shell_command)
    if args.prepare_only:
        print('\n[OK] 已生成包装层文件，未启动 compiler。'); return 0
    result = run_generation(run_dir)
    wrapper_log = run_dir / 'wrapper.log'
    wrapper_log.write_text(result.stdout + result.stderr, encoding='utf-8')
    if result.returncode != 0:
        print(f'\n[ERROR] compiler 执行失败，退出码 {result.returncode}', file=sys.stderr)
        print(f'请查看日志: {wrapper_log}', file=sys.stderr)
        tail = '\n'.join((result.stdout + result.stderr).splitlines()[-40:])
        if tail:
            print('\n--- log tail ---', file=sys.stderr); print(tail, file=sys.stderr)
        return result.returncode
    if wrapper_plan is not None:
        try:
            model_verilog_path = resolve_model_verilog_path(run_dir, spec)
            wrapper_metadata = emit_wrapper_artifacts(run_dir, wrapper_plan, model_verilog_path)
            write_run_artifacts(run_dir, family, spec, config_line, shell_command, args, wrapper_metadata)
        except Exception as exc:
            print(f'\n[ERROR] memory compiler 已完成，但 UHDL wrapper 生成失败: {exc}', file=sys.stderr)
            return 3
    print('\n[OK] compiler 执行完成。')
    print(f'wrapper_log : {wrapper_log}')
    print(f'output_dir  : {run_dir / spec.output_name}')
    if wrapper_metadata is not None:
        print(f"wrapper_rtl : {run_dir / 'wrapper_rtl'}")
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
