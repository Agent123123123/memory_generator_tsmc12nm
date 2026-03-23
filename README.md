# memory_generator_tsmc12nm

> TSMC 12nm Memory Compiler wrapper & automatic UHDL RTL wrapper generator

## Overview

`memgen` provides a unified CLI over the TSMC 12nm memory compiler.  
Given a TSMC-convention memory name, it:

1. Parses and validates the name → family, VT, words, bits, mux, options
2. Generates `config.txt` + `run.sh` → reproducible compiler invocation
3. Calls the TSMC memory compiler → Verilog model, LIB, LEF, GDSII, …
4. (Optional) Computes a tiling plan and generates two-layer RTL wrapper with [UHDL](./uhdl/)

## Supported Families

| Family    | Type                         |
|-----------|------------------------------|
| `1prf`    | 1-Port Register File         |
| `2prf`    | 2-Port Register File         |
| `spsram`  | Single-Port SRAM             |
| `dpsram`  | Dual-Port SRAM               |
| `uhd1prf` | UHD 1-Port Register File     |
| `uhd2prf` | UHD 2-Port Register File     |

## Installation

```bash
# From repo root
pip install -e .
```

Requires Python ≥ 3.10 and `pyslang` (for UHDL VComponent import).

## Quick Start

```bash
# List supported families
memgen families

# Check / parse a memory name
memgen check ts5n12ffcllulvta8x16m1swsho

# Preview tiling plan (no compiler)
memgen plan ts5n12ffcllulvta8x16m1swsho --width 40 --depth 20

# Run compiler (DATASHEET + Verilog output)
memgen generate ts5n12ffcllulvta8x16m1swsho --kits DATASHEET VERILOG

# Full pipeline: compiler + RTL wrapper
memgen run ts5n12ffcllulvta8x16m1swsho my_rf --width 40 --depth 20
```

## CLI Reference

```
memgen --help
memgen families --help
memgen check --help
memgen plan --help
memgen generate --help
memgen run --help
```

## Project Structure

```
memory_generator_tsmc12nm/
├── memgen/
│   ├── cli.py          # CLI entry point (subcommands: families/check/plan/generate/run)
│   ├── wrapper.py      # Compiler orchestration & memory name parser
│   ├── plan.py         # Tiling plan calculation (pure math, no side effects)
│   ├── uhdl_emit.py    # UHDL-based RTL wrapper code generation
│   └── generate.py     # Full-pipeline helper (compiler + wrapper)
├── uhdl/               # UHDL Python HDL framework (submodule)
├── pyproject.toml
└── README.md
```

## Constraints

- **BIST is permanently disabled** across all families (no `b` token in name)
- **ROM is permanently disabled** (`ts3n12ffcll…` prefix is rejected)
- Requires TSMC MC2 license and `module load mc2_n12/2013.12` in the execution environment
