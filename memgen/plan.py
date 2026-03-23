from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


InterfaceClass = Literal["single_port", "one_read_one_write", "dual_port"]


def ceil_div(lhs: int, rhs: int) -> int:
    return (lhs + rhs - 1) // rhs


def ceil_log2(value: int) -> int:
    if value <= 1:
        return 1
    return (value - 1).bit_length()


def interface_class_for_family(family_id: str) -> InterfaceClass:
    if family_id in {"1prf", "spsram", "uhd1prf"}:
        return "single_port"
    if family_id in {"2prf", "uhd2prf"}:
        return "one_read_one_write"
    if family_id == "dpsram":
        return "dual_port"
    raise ValueError(f"Unsupported family_id for wrapper generation: {family_id}")


@dataclass(frozen=True)
class TileMapping:
    row: int
    col: int
    instance_name: str
    data_bit_low: int
    data_bit_high: int
    valid_data_bits: int
    padded_data_bits: int
    depth_start: int
    depth_end: int
    valid_depth_end: int


@dataclass(frozen=True)
class WrapperPlan:
    family_id: str
    interface_class: InterfaceClass
    canonical_name: str
    compiler_version: str
    macro_module_name: str
    child_module_name: str
    top_module_name: str
    child_words: int
    child_bits: int
    child_addr_bits: int
    exposed_width: int
    exposed_depth: int
    exposed_addr_bits: int
    horizontal_tiles: int
    vertical_tiles: int
    padded_width: int
    padded_depth: int
    row_sel_bits: int
    read_latency_cycles: int
    child_wrapper_filename: str
    top_wrapper_filename: str
    mapping_filename: str
    tiles: list[TileMapping]


def build_wrapper_plan(
    family_id: str,
    canonical_name: str,
    compiler_version: str,
    child_words: int,
    child_bits: int,
    exposed_width: int,
    exposed_depth: int,
    top_module_name: str | None = None,
) -> WrapperPlan:
    if exposed_width <= 0 or exposed_depth <= 0:
        raise ValueError("exposed_width and exposed_depth must be positive")
    if child_words <= 0 or child_bits <= 0:
        raise ValueError("child macro dimensions must be positive")

    interface_class = interface_class_for_family(family_id)
    horizontal_tiles = ceil_div(exposed_width, child_bits)
    vertical_tiles = ceil_div(exposed_depth, child_words)
    padded_width = horizontal_tiles * child_bits
    padded_depth = vertical_tiles * child_words

    child_module_name = f"{canonical_name}_tile_wrapper"
    top_module_name = top_module_name or f"{canonical_name}_w{exposed_width}d{exposed_depth}_wrapper"
    macro_module_name = canonical_name.upper()

    tiles: list[TileMapping] = []
    for row in range(vertical_tiles):
        depth_start = row * child_words
        depth_end = depth_start + child_words - 1
        valid_depth_end = min(depth_end, exposed_depth - 1)
        for col in range(horizontal_tiles):
            data_bit_low = col * child_bits
            data_bit_high = min(exposed_width, data_bit_low + child_bits) - 1
            valid_data_bits = data_bit_high - data_bit_low + 1
            padded_data_bits = child_bits - valid_data_bits
            tiles.append(
                TileMapping(
                    row=row,
                    col=col,
                    instance_name=f"u_tile_r{row}_c{col}",
                    data_bit_low=data_bit_low,
                    data_bit_high=data_bit_high,
                    valid_data_bits=valid_data_bits,
                    padded_data_bits=padded_data_bits,
                    depth_start=depth_start,
                    depth_end=depth_end,
                    valid_depth_end=valid_depth_end,
                )
            )

    return WrapperPlan(
        family_id=family_id,
        interface_class=interface_class,
        canonical_name=canonical_name,
        compiler_version=compiler_version,
        macro_module_name=macro_module_name,
        child_module_name=child_module_name,
        top_module_name=top_module_name,
        child_words=child_words,
        child_bits=child_bits,
        child_addr_bits=ceil_log2(child_words),
        exposed_width=exposed_width,
        exposed_depth=exposed_depth,
        exposed_addr_bits=ceil_log2(exposed_depth),
        horizontal_tiles=horizontal_tiles,
        vertical_tiles=vertical_tiles,
        padded_width=padded_width,
        padded_depth=padded_depth,
        row_sel_bits=ceil_log2(vertical_tiles),
        read_latency_cycles=1,
        child_wrapper_filename=f"{child_module_name}.v",
        top_wrapper_filename=f"{top_module_name}.v",
        mapping_filename=f"{top_module_name}.mapping.json",
        tiles=tiles,
    )


def describe_wrapper_plan(plan: WrapperPlan) -> dict:
    return {
        "enabled": True,
        "generator": "uhdl",
        "interface_class": plan.interface_class,
        "read_latency_cycles": plan.read_latency_cycles,
        "top_module_name": plan.top_module_name,
        "child_module_name": plan.child_module_name,
        "macro_module_name": plan.macro_module_name,
        "horizontal_tiles": plan.horizontal_tiles,
        "vertical_tiles": plan.vertical_tiles,
        "padded_width": plan.padded_width,
        "padded_depth": plan.padded_depth,
    }


def plan_as_dict(plan: WrapperPlan) -> dict:
    return asdict(plan)
