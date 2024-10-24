"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import ClassVar, Optional
from opencxl.cxl.component.physical_port_manager import PhysicalPortManager
from opencxl.cxl.component.virtual_switch_manager import VirtualSwitchManager
from opencxl.cxl.cci.common import (
    CCI_FM_API_COMMAND_OPCODE,
)
from opencxl.cxl.component.cci_executor import (
    CciRequest,
    CciResponse,
    CciForegroundCommand,
)
from opencxl.cxl.transport.transaction import CciBasePacket, CciPayloadPacket


class TunnelManagementTargetType(Enum):
    PORT_OR_LD_BASED = 0x00
    LD_POOL_CCI = 0x01


@dataclass
class TunnelManagementRequestPayload:
    port_or_ld_id: int = field(default=0, metadata={"offset": 0, "length": 1})
    target_type: TunnelManagementTargetType = field(
        default=TunnelManagementTargetType(0), metadata={"offset": 1, "length": 1}
    )
    command_size: int = field(default=0, metadata={"offset": 2, "length": 2})
    command_payload: int = field(default=0, metadata={"offset": 4})

    def dump(self) -> bytes:
        data = bytearray(self.command_size + 4)
        data[0:1] = self.port_or_ld_id.to_bytes(1, "little")
        data[1:2] = self.target_type.value.to_bytes(1, "little")
        data[2:4] = self.command_size.to_bytes(2, "little")
        data[4 : 4 + self.command_size] = self.command_payload.to_bytes(self.command_size, "little")
        return bytes(data)

    @classmethod
    def parse(cls, data: bytes):
        port_or_ld_id = int.from_bytes(data[0:1], "little")
        target_type = int.from_bytes(data[1:2], "little")
        command_size = int.from_bytes(data[2:4], "little")
        command_payload = int.from_bytes(data[4:], "little")

        if len(data) != command_size:
            raise ValueError("Provided bytes object does not match the expected data size.")
        return cls(port_or_ld_id, target_type, command_size, command_payload)


@dataclass
class TunnelManagementResponsePayload:
    response_size: int = field(default=0, metadata={"offset": 0, "length": 2})
    reserved: int = field(default=0, metadata={"offset": 2, "length": 2})
    payload: int = field(default=0, metadata={"offset": 4})

    @classmethod
    def parse(cls, data: bytes):
        response_size = int.from_bytes(data[0:2], "little")
        payload = int.from_bytes(data[4:], "little")

        if len(data) != response_size + 4:
            raise ValueError("Provided bytes object does not match the expected data size.")
        return cls(
            response_size,
            payload,
        )

    def dump(self) -> bytes:
        data = bytearray(self.response_size + 4)
        data[0:2] = self.response_size.to_bytes(2, "little")
        data[4 : 4 + self.response_size] = self.payload.to_bytes(self.response_size, "little")
        return bytes(data)

    def get_pretty_print(self) -> str:
        return f"- Tunnel Management Command:\n" f"- Payload Bytes: {self.response_size} Bytes\n"


class TunnelManagementCommand(CciForegroundCommand):
    OPCODE = CCI_FM_API_COMMAND_OPCODE.TUNNEL_MANAGEMENT_COMMAND

    def __init__(
        self,
        physical_port_manager: PhysicalPortManager,
        virtual_switch_manager: VirtualSwitchManager,
        label: Optional[str] = None,
    ):
        super().__init__(self.OPCODE, label=label)
        self._physical_port_manager = physical_port_manager
        self._virtual_switch_manager = virtual_switch_manager

    async def _execute(self, request: CciRequest) -> CciResponse:
        request_payload = self.parse_request_payload(request.payload)
        port_or_ld_id = request_payload.port_or_ld_id
        port_device = self._physical_port_manager.get_port_device(port_or_ld_id)

        real_payload = request_payload.command_payload
        real_payload_len = request_payload.command_size
        to_dev_payload = CciPayloadPacket.create(real_payload, real_payload_len)
        await port_device.get_downstream_connection().cci_fifo.host_to_target.put(to_dev_payload)

        dev_response: CciBasePacket = (
            await port_device.get_downstream_connection().cci_fifo.target_to_host.get()
        )

        payload = TunnelManagementResponsePayload(dev_response.len(), payload=dev_response.data)

        return CciResponse(payload=payload)

    @classmethod
    def create_cci_request(cls, request: TunnelManagementRequestPayload) -> CciRequest:
        return CciRequest(opcode=cls.OPCODE, payload=request.dump())

    @staticmethod
    def parse_request_payload(payload: bytes) -> TunnelManagementRequestPayload:
        return TunnelManagementRequestPayload.parse(payload)

    @staticmethod
    def parse_response_payload(
        payload: bytes,
    ) -> TunnelManagementResponsePayload:
        return TunnelManagementResponsePayload.parse(payload)
