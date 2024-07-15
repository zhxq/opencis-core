"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

# pylint: disable=duplicate-code
import asyncio
import glob
from dataclasses import dataclass, field
import os
from typing import Optional, List
from random import sample
from queue import Queue

from opencxl.util.logger import logger
from opencxl.cxl.transport.memory_fifo import (
    MemoryFifoPair,
    MemoryRequest,
    MemoryResponse,
    MEMORY_REQUEST_TYPE,
)

from opencxl.util.component import RunnableComponent
from opencxl.cxl.component.irq_handler import Irq, IrqHandler
from opencxl.cxl.component.root_complex.root_complex import (
    RootComplex,
    RootComplexConfig,
    RootComplexMemoryControllerConfig,
)
from opencxl.cxl.component.cache_controller import (
    CacheController,
    CacheControllerConfig,
)
from opencxl.cxl.component.root_complex.root_port_client_manager import (
    RootPortClientManager,
    RootPortClientManagerConfig,
    RootPortClientConfig,
)
from opencxl.cxl.component.root_complex.root_port_switch import (
    RootPortSwitchPortConfig,
    COH_POLICY_TYPE,
    ROOT_PORT_SWITCH_TYPE,
)
from opencxl.cxl.component.root_complex.home_agent import MemoryRange
from opencxl.cxl.transport.cache_fifo import CacheFifoPair


@dataclass
class HostTrainIoGenConfig:
    host_name: str
    processor_to_mem_fifo: MemoryFifoPair
    root_complex: RootComplex
    irq_handler: IrqHandler


class HostTrainIoGen(RunnableComponent):
    def __init__(self, config: HostTrainIoGenConfig, sample_from_each_category: int = 5):
        super().__init__(lambda class_name: f"{config.host_name}:{class_name}")
        self._host_name = config.host_name
        self._processor_to_mem_fifo = config.processor_to_mem_fifo
        self._root_complex = config.root_complex
        self._irq_handler = config.irq_handler
        self._validation_results = Queue()
        self._sample_from_each_category = sample_from_each_category
        self._sampled_file_categories = []
        self._total_samples: int = 0

    # pylint: disable=duplicate-code
    async def load(self, address: int, size: int) -> MemoryResponse:
        packet = MemoryRequest(MEMORY_REQUEST_TYPE.READ, address, size)
        await self._processor_to_mem_fifo.request.put(packet)
        packet = await self._processor_to_mem_fifo.response.get()
        return packet

    async def store(self, address: int, size: int, value: int) -> MemoryResponse:
        packet = MemoryRequest(MEMORY_REQUEST_TYPE.WRITE, address, size, value)
        await self._processor_to_mem_fifo.request.put(packet)
        packet = await self._processor_to_mem_fifo.response.get()
        return packet

    async def write_config(self, bdf: int, offset: int, size: int, value: int):
        await self._root_complex.write_config(bdf, offset, size, value)

    async def read_config(self, bdf: int, offset: int, size: int) -> int:
        return await self._root_complex.read_config(bdf, offset, size)

    async def write_mmio(self, address: int, size: int, value: int):
        await self._root_complex.write_mmio(address, size, value)

    async def read_mmio(self, address: int, size: int) -> int:
        return await self._root_complex.read_mmio(address, size)

    async def write_cxl_mem(self, address: int, size: int, value: int):
        await self._root_complex.write_cxl_mem(address, size, value)

    async def read_cxl_mem(self, address: int, size: int) -> int:
        return await self._root_complex.read_cxl_mem(address, size)

    async def _host_process_llc_iogen(self):
        # Pass init-info mem location to the remote using MMIO
        train_data_path = "/Users/zhxq/Downloads/imagenette2-160"

        csv_data_mem_loc = 0x00004000
        csv_data = b""
        with open(f"{train_data_path}/noisy_imagenette.csv", "rb") as f:
            csv_data = f.read()
        csv_data_int = int.from_bytes(csv_data, "big")
        csv_data_len = len(csv_data)
        self.store(csv_data_mem_loc, csv_data_len, csv_data_int)

        self.write_mmio(0x40, 8, csv_data_mem_loc)
        self.write_mmio(0x48, 8, csv_data_len)
        self._irq_handler.send_irq_request(Irq.HOST_READY)

        categories = glob.glob(train_data_path + "/val/*")
        self._total_samples = len(categories) * self._sample_from_each_category

        for c in categories:
            category_pics = glob.glob(f"{c}/*.JPEG")
            sample_pics = sample(category_pics, self._sample_from_each_category)
            category_name = c.split(os.path.sep)[-1]
            self._sampled_file_categories += [category_name] * self._sample_from_each_category
            for s in sample_pics:
                with open(s, "rb") as f:
                    pic_data = f.read()
                    pic_data_int = int.from_bytes(pic_data, "big")
                    pic_data_len = len(pic_data)
                    self.write_cxl_mem(0x00008000, pic_data_len, pic_data_int)
                    self._irq_handler.send_irq_request(Irq.HOST_SENT)
        logger.info(f"[{self._host_name}] Sent Data Done")

    def save_validation_result(self):
        dma = self.read_mmio(0x50, 8)
        dma_len = self.read_mmio(0x58, 8)
        validate_result = self.read_cxl_mem(dma, dma_len)
        self._validation_results.put(validate_result)

        correct = 0

        if self._total_samples == self._validation_results.qsize():
            for i in range(self._total_samples):
                real_category = self._sampled_file_categories[i]
                validated_category = self._validation_results.get()
                print(
                    f"Picture {i} category: Real: {real_category}, validated: {validated_category}"
                )
                if real_category == validated_category:
                    correct += 1

            print("Sampled validation results:")
            print(
                f"Correct/Total: {correct}/{self._total_samples} "
                f"({correct/self._total_samples:.2f}%)"
            )

    async def _run(self):
        tasks = [
            asyncio.create_task(self._host_process_llc_iogen()),
        ]
        await self._change_status_to_running()
        await asyncio.gather(*tasks)

    async def _stop(self):
        await self._processor_to_mem_fifo.response.put(None)


@dataclass
class CxlTrainingHostConfig:
    host_name: str
    root_bus: int
    root_port_switch_type: ROOT_PORT_SWITCH_TYPE
    memory_controller: RootComplexMemoryControllerConfig
    memory_ranges: List[MemoryRange] = field(default_factory=list)
    root_ports: List[RootPortClientConfig] = field(default_factory=list)
    coh_type: Optional[COH_POLICY_TYPE] = COH_POLICY_TYPE.DotMemBI


class CxlTrainingHost(RunnableComponent):
    def __init__(self, config: CxlTrainingHostConfig):
        super().__init__(lambda class_name: f"{config.host_name}:{class_name}")

        processor_to_cache_fifo = MemoryFifoPair()
        processor_to_mem_fifo = MemoryFifoPair()
        cache_to_home_agent_fifo = CacheFifoPair()
        home_agent_to_cache_fifo = CacheFifoPair()
        cache_to_coh_bridge_fifo = CacheFifoPair()
        coh_bridge_to_cache_fifo = CacheFifoPair()

        self._irq_handler = IrqHandler(
            device_name=self._label,
            server_bind_port=9000,
            client_target_port=9100,
        )

        # Create Root Port Client Manager
        root_port_client_manager_config = RootPortClientManagerConfig(
            client_configs=config.root_ports, host_name=config.host_name
        )
        self._root_port_client_manager = RootPortClientManager(root_port_client_manager_config)

        # Create Root Complex
        root_complex_root_ports = [
            RootPortSwitchPortConfig(
                port_index=connection.port_index, downstream_connection=connection.connection
            )
            for connection in self._root_port_client_manager.get_cxl_connections()
        ]
        root_complex_config = RootComplexConfig(
            host_name=config.host_name,
            root_bus=config.root_bus,
            root_port_switch_type=config.root_port_switch_type,
            cache_to_home_agent_fifo=cache_to_home_agent_fifo,
            home_agent_to_cache_fifo=home_agent_to_cache_fifo,
            cache_to_coh_bridge_fifo=cache_to_coh_bridge_fifo,
            coh_bridge_to_cache_fifo=coh_bridge_to_cache_fifo,
            memory_controller=config.memory_controller,
            memory_ranges=[],
            root_ports=root_complex_root_ports,
        )
        self._root_complex = RootComplex(root_complex_config)

        if config.coh_type == COH_POLICY_TYPE.DotCache:
            cache_to_coh_agent_fifo = cache_to_coh_bridge_fifo
            coh_agent_to_cache_fifo = coh_bridge_to_cache_fifo
        elif config.coh_type == COH_POLICY_TYPE.DotMemBI:
            cache_to_coh_agent_fifo = cache_to_home_agent_fifo
            coh_agent_to_cache_fifo = home_agent_to_cache_fifo

        cache_controller_config = CacheControllerConfig(
            component_name=config.host_name,
            processor_to_cache_fifo=processor_to_cache_fifo,
            cache_to_coh_agent_fifo=cache_to_coh_agent_fifo,
            coh_agent_to_cache_fifo=coh_agent_to_cache_fifo,
            cache_num_assoc=4,
            cache_num_set=8,
        )
        self._cache_controller = CacheController(cache_controller_config)

        host_processor_config = HostTrainIoGenConfig(
            host_name=config.host_name,
            processor_to_mem_fifo=processor_to_mem_fifo,
            root_complex=self.get_root_complex(),
            irq_handler=self._irq_handler,
        )
        self._host_simple_processor = HostTrainIoGen(host_processor_config)

        self._irq_handler.register_interrupt_handler(
            Irq.ACCEL_READY, self._host_simple_processor.save_validation_result()
        )

    def get_root_complex(self):
        return self._root_complex

    async def _run(self):
        run_tasks = [
            asyncio.create_task(self._root_port_client_manager.run()),
            asyncio.create_task(self._root_complex.run()),
            asyncio.create_task(self._cache_controller.run()),
            asyncio.create_task(self._host_simple_processor.run()),
            asyncio.create_task(self._irq_handler.run()),
        ]
        wait_tasks = [
            asyncio.create_task(self._root_port_client_manager.wait_for_ready()),
            asyncio.create_task(self._root_complex.wait_for_ready()),
            asyncio.create_task(self._cache_controller.wait_for_ready()),
            asyncio.create_task(self._host_simple_processor.wait_for_ready()),
            asyncio.create_task(self._irq_handler.wait_for_ready()),
        ]
        await asyncio.gather(*wait_tasks)
        await self._change_status_to_running()
        await asyncio.gather(*run_tasks)

    async def _stop(self):
        tasks = [
            asyncio.create_task(self._root_port_client_manager.stop()),
            asyncio.create_task(self._root_complex.stop()),
            asyncio.create_task(self._cache_controller.stop()),
            asyncio.create_task(self._host_simple_processor.stop()),
            asyncio.create_task(self._irq_handler.stop()),
        ]
        await asyncio.gather(*tasks)
