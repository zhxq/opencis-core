"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from opencxl.cxl.component.common import CXL_COMPONENT_TYPE
from opencxl.cxl.component.virtual_switch.routing_table import RoutingTable
from opencxl.cxl.component.cxl_bridge_component import (
    CxlDownstreamPortComponent,
)
from opencxl.cxl.component.virtual_switch.vppb import Vppb


# DownstreamVppb class will have many similar methods to DownstreamPortDevice class
# pylint: disable=duplicate-code
class DownstreamVppb(Vppb):
    def __init__(self, vppb_index: int, vcs_id: int):
        super().__init__()
        self._vppb_index = vppb_index
        self._vcs_id = vcs_id

    def _get_label(self) -> str:
        vcs_str = f"VCS{self._vcs_id}"
        vppb_str = f"vPPB{self._vppb_index}(DSP)"
        return f"{vcs_str}:{vppb_str}"

    def _create_message(self, message: str) -> str:
        message = f"[{self.__class__.__name__}:{self._get_label()}] {message}"
        return message

    def get_reg_vals(self):
        return self._cxl_io_manager.get_cfg_reg_vals()

    def set_vppb_index(self, vppb_index: int):
        self._vppb_index = vppb_index
        self._pci_bridge_component.set_port_number(self._vppb_index)

    def get_device_type(self) -> CXL_COMPONENT_TYPE:
        return CXL_COMPONENT_TYPE.DSP

    def set_routing_table(self, routing_table: RoutingTable):
        self._pci_bridge_component.set_routing_table(routing_table)

    def get_secondary_bus_number(self):
        return self._pci_registers.pci.secondary_bus_number

    def get_cxl_component(self) -> CxlDownstreamPortComponent:
        return self._cxl_component
