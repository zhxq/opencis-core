"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from asyncio import gather, create_task
import pytest

from opencxl.cxl.device.cxl_type3_device import CxlType3Device, CXL_T3_DEV_TYPE
from opencxl.cxl.device.root_port_device import CxlRootPortDevice
from opencxl.cxl.component.cxl_connection import CxlConnection
from opencxl.util.number_const import MB


def test_single_logical_device():
    memory_size = 256 * MB
    memory_file = "mem.bin"
    transport_connection = CxlConnection()
    CxlType3Device(
        transport_connection,
        memory_size=memory_size,
        memory_file=memory_file,
        dev_type=CXL_T3_DEV_TYPE.SLD,
    )


@pytest.mark.asyncio
async def test_single_logical_device_run_stop(get_gold_std_reg_vals):
    memory_size = 256 * MB
    memory_file = "mem.bin"
    transport_connection = CxlConnection()
    device = CxlType3Device(
        transport_connection,
        memory_size=memory_size,
        memory_file=memory_file,
        dev_type=CXL_T3_DEV_TYPE.SLD,
    )

    # check register values after initialization
    reg_vals = str(device.get_reg_vals())
    reg_vals_expected = get_gold_std_reg_vals("SLD")
    assert reg_vals == reg_vals_expected

    async def wait_and_stop():
        await device.wait_for_ready()
        await device.stop()

    tasks = [create_task(device.run()), create_task(wait_and_stop())]
    await gather(*tasks)


@pytest.mark.asyncio
async def test_single_logical_device_enumeration():
    memory_size = 256 * MB
    memory_file = "mem.bin"
    transport_connection = CxlConnection()
    root_port_device = CxlRootPortDevice(downstream_connection=transport_connection, label="Port0")
    device = CxlType3Device(
        transport_connection,
        memory_size=memory_size,
        memory_file=memory_file,
        dev_type=CXL_T3_DEV_TYPE.SLD,
    )
    memory_base_address = 0xFE000000

    async def wait_and_stop():
        await device.wait_for_ready()
        await root_port_device.enumerate(memory_base_address)
        await device.stop()

    tasks = [create_task(device.run()), create_task(wait_and_stop())]
    await gather(*tasks)
