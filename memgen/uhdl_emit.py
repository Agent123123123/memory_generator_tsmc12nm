from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from .plan import WrapperPlan, describe_wrapper_plan, plan_as_dict


REPO_ROOT = Path(__file__).resolve().parent.parent
LOCAL_UHDL_ROOT = REPO_ROOT / "uhdl"
if str(LOCAL_UHDL_ROOT) not in sys.path:
    sys.path.insert(0, str(LOCAL_UHDL_ROOT))

from uhdl import (  # type: ignore  # noqa: E402
    And,
    Case,
    Combine,
    Component,
    Cut,
    EmptyWhen,
    Equal,
    Input,
    Inverse,
    Less,
    Output,
    Reg,
    UInt,
    VComponent,
    Wire,
    when,
)


def _const(width: int, value: int) -> UInt:
    return UInt(width, value)


def _slice(signal, high: int, low: int = 0):
    return Cut(signal, high, low)


def _combine(parts: list):
    if len(parts) == 1:
        return parts[0]
    return Combine(*parts)


def _and_all(parts: list):
    if len(parts) == 1:
        return parts[0]
    return And(*parts)


def _full_ones(width: int) -> int:
    return (1 << width) - 1


def _build_padded_slice(bus, low: int, high: int, full_width: int, pad_with_ones: bool):
    valid_width = high - low + 1
    sliced = _slice(bus, high, low)
    if valid_width == full_width:
        return sliced
    pad_width = full_width - valid_width
    pad_value = _full_ones(pad_width) if pad_with_ones else 0
    return _combine([_const(pad_width, pad_value), sliced])


def _build_row_decode(addr, plan: WrapperPlan):
    if plan.vertical_tiles == 1:
        return _const(plan.row_sel_bits, 0), _slice(addr, plan.child_addr_bits - 1, 0)

    row_sel_mux = EmptyWhen()
    local_addr_mux = EmptyWhen()
    for row in range(plan.vertical_tiles - 1):
        upper_bound = (row + 1) * plan.child_words
        base_addr = row * plan.child_words
        cond = Less(addr, _const(plan.exposed_addr_bits, upper_bound))
        row_sel_mux = row_sel_mux.when(cond).then(_const(plan.row_sel_bits, row))
        local_addr_mux = local_addr_mux.when(cond).then(
            _slice(addr - _const(plan.exposed_addr_bits, base_addr), plan.child_addr_bits - 1, 0)
        )
    last_row = plan.vertical_tiles - 1
    last_base_addr = last_row * plan.child_words
    row_sel_mux.otherwise(_const(plan.row_sel_bits, last_row))
    local_addr_mux.otherwise(_slice(addr - _const(plan.exposed_addr_bits, last_base_addr), plan.child_addr_bits - 1, 0))
    return row_sel_mux, local_addr_mux


class NamedComponent(Component):
    def __init__(self, module_name: str):
        self._module_name_override = module_name
        super().__init__()

    @property
    def module_name(self):
        return self._module_name_override


class SinglePortTileWrapper(NamedComponent):
    def __init__(self, plan: WrapperPlan, model_verilog: Path):
        self._plan = plan
        self._model_verilog = Path(model_verilog)
        super().__init__(plan.child_module_name)

    def circuit(self):
        plan = self._plan
        self.clk = Input(UInt(1))
        self.ceb = Input(UInt(1))
        self.web = Input(UInt(1))
        self.addr = Input(UInt(plan.child_addr_bits))
        self.din = Input(UInt(plan.child_bits))
        self.bwe = Input(UInt(plan.child_bits))
        self.slp = Input(UInt(1))
        self.dslp = Input(UInt(1))
        self.sd = Input(UInt(1))
        self.rt_sel = Input(UInt(2))
        self.wt_sel = Input(UInt(2))
        self.q = Output(UInt(plan.child_bits))
        self.pu_delay = Output(UInt(1))

        self.u_macro = VComponent(file=str(self._model_verilog), top=plan.macro_module_name)

        self.u_macro.SLP += self.slp
        self.u_macro.DSLP += self.dslp
        self.u_macro.SD += self.sd
        self.u_macro.CLK += self.clk
        self.u_macro.CEB += self.ceb
        self.u_macro.WEB += self.web
        self.u_macro.A += self.addr
        self.u_macro.D += self.din
        self.u_macro.BWEB += self.bwe
        self.u_macro.RTSEL += self.rt_sel
        self.u_macro.WTSEL += self.wt_sel
        self.q += self.u_macro.Q
        self.pu_delay += self.u_macro.PUDELAY


class OneReadOneWriteTileWrapper(NamedComponent):
    def __init__(self, plan: WrapperPlan, model_verilog: Path):
        self._plan = plan
        self._model_verilog = Path(model_verilog)
        super().__init__(plan.child_module_name)

    def circuit(self):
        plan = self._plan
        is_uhd2prf = plan.family_id == "uhd2prf"

        if is_uhd2prf:
            self.clk = Input(UInt(1))
        else:
            self.clkw = Input(UInt(1))
            self.clkr = Input(UInt(1))

        self.web = Input(UInt(1))
        self.waddr = Input(UInt(plan.child_addr_bits))
        self.din = Input(UInt(plan.child_bits))
        self.bwe = Input(UInt(plan.child_bits))
        self.reb = Input(UInt(1))
        self.raddr = Input(UInt(plan.child_addr_bits))
        if is_uhd2prf:
            self.rt_sel = Input(UInt(2))
            self.wt_sel = Input(UInt(2))
            self.mt_sel = Input(UInt(2))
        else:
            self.rct = Input(UInt(2))
            self.wct = Input(UInt(2))
            self.kp = Input(UInt(3))
        self.slp = Input(UInt(1))
        self.dslp = Input(UInt(1))
        self.sd = Input(UInt(1))
        self.q = Output(UInt(plan.child_bits))
        self.pu_delay = Output(UInt(1))

        self.u_macro = VComponent(file=str(self._model_verilog), top=plan.macro_module_name)

        self.u_macro.AA += self.waddr
        self.u_macro.D += self.din
        self.u_macro.BWEB += self.bwe
        self.u_macro.WEB += self.web
        self.u_macro.AB += self.raddr
        self.u_macro.REB += self.reb
        if is_uhd2prf:
            self.u_macro.CLK += self.clk
            self.u_macro.RTSEL += self.rt_sel
            self.u_macro.WTSEL += self.wt_sel
            self.u_macro.MTSEL += self.mt_sel
        else:
            self.u_macro.CLKW += self.clkw
            self.u_macro.CLKR += self.clkr
            self.u_macro.RCT += self.rct
            self.u_macro.WCT += self.wct
            self.u_macro.KP += self.kp
        self.u_macro.SLP += self.slp
        self.u_macro.DSLP += self.dslp
        self.u_macro.SD += self.sd
        self.q += self.u_macro.Q
        self.pu_delay += self.u_macro.PUDELAY


class DualPortTileWrapper(NamedComponent):
    def __init__(self, plan: WrapperPlan, model_verilog: Path):
        self._plan = plan
        self._model_verilog = Path(model_verilog)
        super().__init__(plan.child_module_name)

    def circuit(self):
        plan = self._plan
        self.slp = Input(UInt(1))
        self.dslp = Input(UInt(1))
        self.sd = Input(UInt(1))
        self.wt_sel = Input(UInt(2))
        self.rt_sel = Input(UInt(2))
        self.aa = Input(UInt(plan.child_addr_bits))
        self.da = Input(UInt(plan.child_bits))
        self.bweba = Input(UInt(plan.child_bits))
        self.weba = Input(UInt(1))
        self.ceba = Input(UInt(1))
        self.clka = Input(UInt(1))
        self.ab = Input(UInt(plan.child_addr_bits))
        self.db = Input(UInt(plan.child_bits))
        self.bwebb = Input(UInt(plan.child_bits))
        self.webb = Input(UInt(1))
        self.cebb = Input(UInt(1))
        self.clkb = Input(UInt(1))
        self.qa = Output(UInt(plan.child_bits))
        self.qb = Output(UInt(plan.child_bits))
        self.pu_delay = Output(UInt(1))

        self.u_macro = VComponent(file=str(self._model_verilog), top=plan.macro_module_name)

        self.u_macro.SLP += self.slp
        self.u_macro.DSLP += self.dslp
        self.u_macro.SD += self.sd
        self.u_macro.WTSEL += self.wt_sel
        self.u_macro.RTSEL += self.rt_sel
        self.u_macro.AA += self.aa
        self.u_macro.DA += self.da
        self.u_macro.BWEBA += self.bweba
        self.u_macro.WEBA += self.weba
        self.u_macro.CEBA += self.ceba
        self.u_macro.CLKA += self.clka
        self.u_macro.AB += self.ab
        self.u_macro.DB += self.db
        self.u_macro.BWEBB += self.bwebb
        self.u_macro.WEBB += self.webb
        self.u_macro.CEBB += self.cebb
        self.u_macro.CLKB += self.clkb
        self.qa += self.u_macro.QA
        self.qb += self.u_macro.QB
        self.pu_delay += self.u_macro.PUDELAY


class SinglePortTopWrapper(NamedComponent):
    def __init__(self, plan: WrapperPlan, model_verilog: Path):
        self._plan = plan
        self._model_verilog = Path(model_verilog)
        super().__init__(plan.top_module_name)

    def circuit(self):
        plan = self._plan
        self.clk = Input(UInt(1))
        self.ceb = Input(UInt(1))
        self.web = Input(UInt(1))
        self.addr = Input(UInt(plan.exposed_addr_bits))
        self.din = Input(UInt(plan.exposed_width))
        self.bwe = Input(UInt(plan.exposed_width))
        self.slp = Input(UInt(1))
        self.dslp = Input(UInt(1))
        self.sd = Input(UInt(1))
        self.rt_sel = Input(UInt(2))
        self.wt_sel = Input(UInt(2))
        self.q = Output(UInt(plan.exposed_width))
        self.pu_delay = Output(UInt(1))

        self.access_valid = Wire(UInt(1))
        self.access_valid += Less(self.addr, _const(plan.exposed_addr_bits, plan.exposed_depth))

        access_row_sel_expr, access_local_addr_expr = _build_row_decode(self.addr, plan)
        self.access_row_sel = Wire(UInt(plan.row_sel_bits))
        self.access_local_addr = Wire(UInt(plan.child_addr_bits))
        self.access_row_sel += access_row_sel_expr
        self.access_local_addr += access_local_addr_expr

        self.read_valid_d = Reg(UInt(1), self.clk, None)
        self.read_valid_d += And(Inverse(self.ceb), self.web, self.access_valid)
        self.read_row_sel_d = Reg(UInt(plan.row_sel_bits), self.clk, None)
        self.read_row_sel_d += self.access_row_sel

        tile_pu_delay_list = []
        row_q_buses = []

        for col in range(plan.horizontal_tiles):
            low = col * plan.child_bits
            high = min(plan.exposed_width, low + plan.child_bits) - 1
            din_wire = self.set(f"tile_col_{col}_din", Wire(UInt(plan.child_bits)))
            bwe_wire = self.set(f"tile_col_{col}_bwe", Wire(UInt(plan.child_bits)))
            din_wire += _build_padded_slice(self.din, low, high, plan.child_bits, pad_with_ones=False)
            bwe_wire += _build_padded_slice(self.bwe, low, high, plan.child_bits, pad_with_ones=True)

        for row in range(plan.vertical_tiles):
            row_q_parts = []
            for col in range(plan.horizontal_tiles):
                tile = self.set(f"u_tile_r{row}_c{col}", SinglePortTileWrapper(plan, self._model_verilog))
                selected = self.set(f"tile_r{row}_c{col}_selected", Wire(UInt(1)))
                tile_ceb = self.set(f"tile_r{row}_c{col}_ceb", Wire(UInt(1)))
                tile_q = self.set(f"tile_r{row}_c{col}_q", Wire(UInt(plan.child_bits)))
                tile_pu_delay = self.set(f"tile_r{row}_c{col}_pu_delay", Wire(UInt(1)))

                selected += And(self.access_valid, Equal(self.access_row_sel, _const(plan.row_sel_bits, row)))
                tile_ceb += when(selected).then(self.ceb).otherwise(_const(1, 1))

                tile.clk += self.clk
                tile.ceb += tile_ceb
                tile.web += self.web
                tile.addr += self.access_local_addr
                tile.din += self.get(f"tile_col_{col}_din")
                tile.bwe += self.get(f"tile_col_{col}_bwe")
                tile.slp += self.slp
                tile.dslp += self.dslp
                tile.sd += self.sd
                tile.rt_sel += self.rt_sel
                tile.wt_sel += self.wt_sel
                tile_q += tile.q
                tile_pu_delay += tile.pu_delay

                row_q_parts.insert(0, tile_q)
                tile_pu_delay_list.append(tile_pu_delay)

            row_bus = self.set(f"row_{row}_q_data", Wire(UInt(plan.padded_width)))
            row_bus += _combine(row_q_parts)
            row_q_buses.append(row_bus)

        case_pairs = [(_const(plan.row_sel_bits, row), row_q_buses[row]) for row in range(plan.vertical_tiles)]
        self.read_data_padded = Wire(UInt(plan.padded_width))
        self.read_data_padded += Case(self.read_row_sel_d, case_pairs, _const(plan.padded_width, 0))

        q_expr = self.read_data_padded if plan.padded_width == plan.exposed_width else _slice(self.read_data_padded, plan.exposed_width - 1, 0)
        self.q += when(self.read_valid_d).then(q_expr).otherwise(_const(plan.exposed_width, 0))
        self.pu_delay += _and_all(tile_pu_delay_list)


class OneReadOneWriteTopWrapper(NamedComponent):
    def __init__(self, plan: WrapperPlan, model_verilog: Path):
        self._plan = plan
        self._model_verilog = Path(model_verilog)
        super().__init__(plan.top_module_name)

    def circuit(self):
        plan = self._plan
        is_uhd2prf = plan.family_id == "uhd2prf"

        if is_uhd2prf:
            self.clk = Input(UInt(1))
        else:
            self.clkw = Input(UInt(1))
            self.clkr = Input(UInt(1))
        self.web = Input(UInt(1))
        self.waddr = Input(UInt(plan.exposed_addr_bits))
        self.din = Input(UInt(plan.exposed_width))
        self.bwe = Input(UInt(plan.exposed_width))
        self.reb = Input(UInt(1))
        self.raddr = Input(UInt(plan.exposed_addr_bits))
        if is_uhd2prf:
            self.rt_sel = Input(UInt(2))
            self.wt_sel = Input(UInt(2))
            self.mt_sel = Input(UInt(2))
        else:
            self.rct = Input(UInt(2))
            self.wct = Input(UInt(2))
            self.kp = Input(UInt(3))
        self.slp = Input(UInt(1))
        self.dslp = Input(UInt(1))
        self.sd = Input(UInt(1))
        self.q = Output(UInt(plan.exposed_width))
        self.pu_delay = Output(UInt(1))

        self.write_valid = Wire(UInt(1))
        self.write_valid += Less(self.waddr, _const(plan.exposed_addr_bits, plan.exposed_depth))
        self.read_valid = Wire(UInt(1))
        self.read_valid += Less(self.raddr, _const(plan.exposed_addr_bits, plan.exposed_depth))

        write_row_sel_expr, write_local_addr_expr = _build_row_decode(self.waddr, plan)
        read_row_sel_expr, read_local_addr_expr = _build_row_decode(self.raddr, plan)

        self.write_row_sel = Wire(UInt(plan.row_sel_bits))
        self.write_local_addr = Wire(UInt(plan.child_addr_bits))
        self.read_row_sel = Wire(UInt(plan.row_sel_bits))
        self.read_local_addr = Wire(UInt(plan.child_addr_bits))
        self.write_row_sel += write_row_sel_expr
        self.write_local_addr += write_local_addr_expr
        self.read_row_sel += read_row_sel_expr
        self.read_local_addr += read_local_addr_expr

        read_clk = self.clk if is_uhd2prf else self.clkr
        self.read_valid_d = Reg(UInt(1), read_clk, None)
        self.read_valid_d += And(Inverse(self.reb), self.read_valid)
        self.read_row_sel_d = Reg(UInt(plan.row_sel_bits), read_clk, None)
        self.read_row_sel_d += self.read_row_sel

        tile_pu_delay_list = []
        row_q_buses = []

        for col in range(plan.horizontal_tiles):
            low = col * plan.child_bits
            high = min(plan.exposed_width, low + plan.child_bits) - 1
            din_wire = self.set(f"tile_col_{col}_din", Wire(UInt(plan.child_bits)))
            bwe_wire = self.set(f"tile_col_{col}_bwe", Wire(UInt(plan.child_bits)))
            din_wire += _build_padded_slice(self.din, low, high, plan.child_bits, pad_with_ones=False)
            bwe_wire += _build_padded_slice(self.bwe, low, high, plan.child_bits, pad_with_ones=True)

        for row in range(plan.vertical_tiles):
            row_q_parts = []
            for col in range(plan.horizontal_tiles):
                tile = self.set(f"u_tile_r{row}_c{col}", OneReadOneWriteTileWrapper(plan, self._model_verilog))
                write_selected = self.set(f"tile_r{row}_c{col}_write_selected", Wire(UInt(1)))
                read_selected = self.set(f"tile_r{row}_c{col}_read_selected", Wire(UInt(1)))
                tile_web = self.set(f"tile_r{row}_c{col}_web", Wire(UInt(1)))
                tile_reb = self.set(f"tile_r{row}_c{col}_reb", Wire(UInt(1)))
                tile_q = self.set(f"tile_r{row}_c{col}_q", Wire(UInt(plan.child_bits)))
                tile_pu_delay = self.set(f"tile_r{row}_c{col}_pu_delay", Wire(UInt(1)))

                write_selected += And(self.write_valid, Equal(self.write_row_sel, _const(plan.row_sel_bits, row)))
                read_selected += And(self.read_valid, Equal(self.read_row_sel, _const(plan.row_sel_bits, row)))
                tile_web += when(write_selected).then(self.web).otherwise(_const(1, 1))
                tile_reb += when(read_selected).then(self.reb).otherwise(_const(1, 1))

                if is_uhd2prf:
                    tile.clk += self.clk
                else:
                    tile.clkw += self.clkw
                tile.web += tile_web
                tile.waddr += self.write_local_addr
                tile.din += self.get(f"tile_col_{col}_din")
                tile.bwe += self.get(f"tile_col_{col}_bwe")
                if not is_uhd2prf:
                    tile.clkr += self.clkr
                tile.reb += tile_reb
                tile.raddr += self.read_local_addr
                if is_uhd2prf:
                    tile.rt_sel += self.rt_sel
                    tile.wt_sel += self.wt_sel
                    tile.mt_sel += self.mt_sel
                else:
                    tile.rct += self.rct
                    tile.wct += self.wct
                    tile.kp += self.kp
                tile.slp += self.slp
                tile.dslp += self.dslp
                tile.sd += self.sd
                tile_q += tile.q
                tile_pu_delay += tile.pu_delay

                row_q_parts.insert(0, tile_q)
                tile_pu_delay_list.append(tile_pu_delay)

            row_bus = self.set(f"row_{row}_q_data", Wire(UInt(plan.padded_width)))
            row_bus += _combine(row_q_parts)
            row_q_buses.append(row_bus)

        case_pairs = [(_const(plan.row_sel_bits, row), row_q_buses[row]) for row in range(plan.vertical_tiles)]
        self.read_data_padded = Wire(UInt(plan.padded_width))
        self.read_data_padded += Case(self.read_row_sel_d, case_pairs, _const(plan.padded_width, 0))

        q_expr = self.read_data_padded if plan.padded_width == plan.exposed_width else _slice(self.read_data_padded, plan.exposed_width - 1, 0)
        self.q += when(self.read_valid_d).then(q_expr).otherwise(_const(plan.exposed_width, 0))
        self.pu_delay += _and_all(tile_pu_delay_list)


class DualPortTopWrapper(NamedComponent):
    def __init__(self, plan: WrapperPlan, model_verilog: Path):
        self._plan = plan
        self._model_verilog = Path(model_verilog)
        super().__init__(plan.top_module_name)

    def circuit(self):
        plan = self._plan
        self.slp = Input(UInt(1))
        self.dslp = Input(UInt(1))
        self.sd = Input(UInt(1))
        self.wt_sel = Input(UInt(2))
        self.rt_sel = Input(UInt(2))
        self.aa = Input(UInt(plan.exposed_addr_bits))
        self.da = Input(UInt(plan.exposed_width))
        self.bweba = Input(UInt(plan.exposed_width))
        self.weba = Input(UInt(1))
        self.ceba = Input(UInt(1))
        self.clka = Input(UInt(1))
        self.ab = Input(UInt(plan.exposed_addr_bits))
        self.db = Input(UInt(plan.exposed_width))
        self.bwebb = Input(UInt(plan.exposed_width))
        self.webb = Input(UInt(1))
        self.cebb = Input(UInt(1))
        self.clkb = Input(UInt(1))
        self.qa = Output(UInt(plan.exposed_width))
        self.qb = Output(UInt(plan.exposed_width))
        self.pu_delay = Output(UInt(1))

        self.a_addr_valid = Wire(UInt(1))
        self.b_addr_valid = Wire(UInt(1))
        self.a_addr_valid += Less(self.aa, _const(plan.exposed_addr_bits, plan.exposed_depth))
        self.b_addr_valid += Less(self.ab, _const(plan.exposed_addr_bits, plan.exposed_depth))

        a_row_sel_expr, a_local_addr_expr = _build_row_decode(self.aa, plan)
        b_row_sel_expr, b_local_addr_expr = _build_row_decode(self.ab, plan)
        self.a_row_sel = Wire(UInt(plan.row_sel_bits))
        self.b_row_sel = Wire(UInt(plan.row_sel_bits))
        self.a_local_addr = Wire(UInt(plan.child_addr_bits))
        self.b_local_addr = Wire(UInt(plan.child_addr_bits))
        self.a_row_sel += a_row_sel_expr
        self.b_row_sel += b_row_sel_expr
        self.a_local_addr += a_local_addr_expr
        self.b_local_addr += b_local_addr_expr

        self.a_read_valid_d = Reg(UInt(1), self.clka, None)
        self.b_read_valid_d = Reg(UInt(1), self.clkb, None)
        self.a_row_sel_d = Reg(UInt(plan.row_sel_bits), self.clka, None)
        self.b_row_sel_d = Reg(UInt(plan.row_sel_bits), self.clkb, None)
        self.a_read_valid_d += And(Inverse(self.ceba), self.weba, self.a_addr_valid)
        self.b_read_valid_d += And(Inverse(self.cebb), self.webb, self.b_addr_valid)
        self.a_row_sel_d += self.a_row_sel
        self.b_row_sel_d += self.b_row_sel

        tile_pu_delay_list = []
        row_qa_buses = []
        row_qb_buses = []

        for col in range(plan.horizontal_tiles):
            low = col * plan.child_bits
            high = min(plan.exposed_width, low + plan.child_bits) - 1
            da_wire = self.set(f"tile_col_a_{col}_din", Wire(UInt(plan.child_bits)))
            bwea_wire = self.set(f"tile_col_a_{col}_bwe", Wire(UInt(plan.child_bits)))
            db_wire = self.set(f"tile_col_b_{col}_din", Wire(UInt(plan.child_bits)))
            bweb_wire = self.set(f"tile_col_b_{col}_bwe", Wire(UInt(plan.child_bits)))
            da_wire += _build_padded_slice(self.da, low, high, plan.child_bits, pad_with_ones=False)
            bwea_wire += _build_padded_slice(self.bweba, low, high, plan.child_bits, pad_with_ones=True)
            db_wire += _build_padded_slice(self.db, low, high, plan.child_bits, pad_with_ones=False)
            bweb_wire += _build_padded_slice(self.bwebb, low, high, plan.child_bits, pad_with_ones=True)

        for row in range(plan.vertical_tiles):
            row_qa_parts = []
            row_qb_parts = []
            for col in range(plan.horizontal_tiles):
                tile = self.set(f"u_tile_r{row}_c{col}", DualPortTileWrapper(plan, self._model_verilog))
                a_selected = self.set(f"tile_r{row}_c{col}_a_selected", Wire(UInt(1)))
                b_selected = self.set(f"tile_r{row}_c{col}_b_selected", Wire(UInt(1)))
                tile_ceba = self.set(f"tile_r{row}_c{col}_ceba", Wire(UInt(1)))
                tile_cebb = self.set(f"tile_r{row}_c{col}_cebb", Wire(UInt(1)))
                tile_weba = self.set(f"tile_r{row}_c{col}_weba", Wire(UInt(1)))
                tile_webb = self.set(f"tile_r{row}_c{col}_webb", Wire(UInt(1)))
                tile_qa = self.set(f"tile_r{row}_c{col}_qa", Wire(UInt(plan.child_bits)))
                tile_qb = self.set(f"tile_r{row}_c{col}_qb", Wire(UInt(plan.child_bits)))
                tile_pu_delay = self.set(f"tile_r{row}_c{col}_pu_delay", Wire(UInt(1)))

                a_selected += And(self.a_addr_valid, Equal(self.a_row_sel, _const(plan.row_sel_bits, row)))
                b_selected += And(self.b_addr_valid, Equal(self.b_row_sel, _const(plan.row_sel_bits, row)))
                tile_ceba += when(a_selected).then(self.ceba).otherwise(_const(1, 1))
                tile_cebb += when(b_selected).then(self.cebb).otherwise(_const(1, 1))
                tile_weba += when(a_selected).then(self.weba).otherwise(_const(1, 1))
                tile_webb += when(b_selected).then(self.webb).otherwise(_const(1, 1))

                tile.slp += self.slp
                tile.dslp += self.dslp
                tile.sd += self.sd
                tile.wt_sel += self.wt_sel
                tile.rt_sel += self.rt_sel
                tile.aa += self.a_local_addr
                tile.da += self.get(f"tile_col_a_{col}_din")
                tile.bweba += self.get(f"tile_col_a_{col}_bwe")
                tile.weba += tile_weba
                tile.ceba += tile_ceba
                tile.clka += self.clka
                tile.ab += self.b_local_addr
                tile.db += self.get(f"tile_col_b_{col}_din")
                tile.bwebb += self.get(f"tile_col_b_{col}_bwe")
                tile.webb += tile_webb
                tile.cebb += tile_cebb
                tile.clkb += self.clkb
                tile_qa += tile.qa
                tile_qb += tile.qb
                tile_pu_delay += tile.pu_delay

                row_qa_parts.insert(0, tile_qa)
                row_qb_parts.insert(0, tile_qb)
                tile_pu_delay_list.append(tile_pu_delay)

            row_qa_bus = self.set(f"row_{row}_qa_data", Wire(UInt(plan.padded_width)))
            row_qb_bus = self.set(f"row_{row}_qb_data", Wire(UInt(plan.padded_width)))
            row_qa_bus += _combine(row_qa_parts)
            row_qb_bus += _combine(row_qb_parts)
            row_qa_buses.append(row_qa_bus)
            row_qb_buses.append(row_qb_bus)

        qa_case_pairs = [(_const(plan.row_sel_bits, row), row_qa_buses[row]) for row in range(plan.vertical_tiles)]
        qb_case_pairs = [(_const(plan.row_sel_bits, row), row_qb_buses[row]) for row in range(plan.vertical_tiles)]
        self.qa_padded = Wire(UInt(plan.padded_width))
        self.qb_padded = Wire(UInt(plan.padded_width))
        self.qa_padded += Case(self.a_row_sel_d, qa_case_pairs, _const(plan.padded_width, 0))
        self.qb_padded += Case(self.b_row_sel_d, qb_case_pairs, _const(plan.padded_width, 0))

        qa_expr = self.qa_padded if plan.padded_width == plan.exposed_width else _slice(self.qa_padded, plan.exposed_width - 1, 0)
        qb_expr = self.qb_padded if plan.padded_width == plan.exposed_width else _slice(self.qb_padded, plan.exposed_width - 1, 0)
        self.qa += when(self.a_read_valid_d).then(qa_expr).otherwise(_const(plan.exposed_width, 0))
        self.qb += when(self.b_read_valid_d).then(qb_expr).otherwise(_const(plan.exposed_width, 0))
        self.pu_delay += _and_all(tile_pu_delay_list)


def _top_component_for(plan: WrapperPlan, model_verilog: Path):
    if plan.interface_class == "single_port":
        return SinglePortTopWrapper(plan, model_verilog)
    if plan.interface_class == "one_read_one_write":
        return OneReadOneWriteTopWrapper(plan, model_verilog)
    if plan.interface_class == "dual_port":
        return DualPortTopWrapper(plan, model_verilog)
    raise ValueError(f"Unsupported interface class: {plan.interface_class}")


def emit_wrapper_artifacts(run_dir: Path, plan: WrapperPlan, model_verilog_path: Path | str) -> dict:
    run_dir = Path(run_dir)
    model_verilog = Path(model_verilog_path)
    if not model_verilog.is_file():
        raise FileNotFoundError(f"memory verilog model not found: {model_verilog}")

    rtl_dir = run_dir / "wrapper_rtl"
    build_dir = rtl_dir / "_uhdl_build"
    rtl_dir.mkdir(parents=True, exist_ok=True)
    build_dir.mkdir(parents=True, exist_ok=True)

    top = _top_component_for(plan, model_verilog)
    top.output_dir = str(build_dir)
    top.generate_verilog(iteration=True)

    generated_dir = build_dir / plan.top_module_name
    child_src = generated_dir / plan.child_wrapper_filename
    top_src = generated_dir / plan.top_wrapper_filename
    if not child_src.is_file() or not top_src.is_file():
        raise RuntimeError(f"UHDL generated files are incomplete under {generated_dir}")

    child_dst = rtl_dir / plan.child_wrapper_filename
    top_dst = rtl_dir / plan.top_wrapper_filename
    mapping_path = rtl_dir / plan.mapping_filename
    filelist_path = rtl_dir / "filelist.f"

    shutil.copyfile(child_src, child_dst)
    shutil.copyfile(top_src, top_dst)

    mapping_payload = plan_as_dict(plan)
    mapping_payload["generator"] = "uhdl"
    mapping_payload["model_verilog_file"] = str(model_verilog)
    mapping_payload["generated_directory"] = str(generated_dir)
    mapping_path.write_text(json.dumps(mapping_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    filelist_path.write_text("\n".join([str(model_verilog), str(child_dst), str(top_dst)]) + "\n", encoding="utf-8")

    metadata = describe_wrapper_plan(plan)
    metadata.update(
        {
            "top_wrapper_file": str(top_dst),
            "child_wrapper_file": str(child_dst),
            "mapping_file": str(mapping_path),
            "filelist": str(filelist_path),
            "model_verilog_file": str(model_verilog),
            "generated_directory": str(generated_dir),
        }
    )
    return metadata
