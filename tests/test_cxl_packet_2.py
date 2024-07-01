"""	
 Copyright (c) 2024, Eeum, Inc.	
 This software is licensed under the terms of the Revised BSD License.	
 See LICENSE for details.	
"""

from opencxl.cxl.transport.transaction import CxlIoMemRdPacket
from opencxl.util.logger import logger


def test_cxl_io_mem_req_packet():
    # pylint: disable=duplicate-code
    addrs = [0x12345678, 0xDEADBEFF, 0xCAFEBABE]
    lens = [4, 8, 12]
    if len(addrs) != len(lens):
        print("Unit test error: #addrs != #lens.")
    for i in range(min(len(addrs), len(lens))):
        packet = CxlIoMemRdPacket.create(addrs[i], lens[i])
        logger.info(packet.mreq_header.addr_lower)
        print(f"lower: 0x{packet.mreq_header.addr_lower:x}")
        print(f"upper: 0x{packet.mreq_header.addr_upper:x}")
        print(f"tag: {packet.mreq_header.tag:x}")
        assert (addrs[i] // 4) * 4 == packet.get_address()
        assert (lens[i]) == packet.get_data_size()
