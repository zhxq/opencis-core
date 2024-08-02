"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

# pylint: disable=duplicate-code, unused-import
import asyncio
import glob
import os
import json
from dataclasses import dataclass, field
from typing import Dict, Optional, List
from random import sample

from opencxl.cxl.component.common import CXL_COMPONENT_TYPE
from opencxl.util.logger import logger
from opencxl.cxl.transport.memory_fifo import (
    MemoryFifoPair,
    MemoryRequest,
    MemoryResponse,
    MEMORY_REQUEST_TYPE,
)

from opencxl.util.component import RunnableComponent
from opencxl.cxl.component.irq_manager import Irq, IrqManager
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
    processor_to_cache_fifo: MemoryFifoPair
    root_complex: RootComplex
    irq_handler: IrqManager
    base_addr: int
    device_count: int
    interleave_gran: int
    device_type: CXL_COMPONENT_TYPE.T1
    cache_controller: CacheController


class HostTrainIoGen(RunnableComponent):
    def __init__(self, config: HostTrainIoGenConfig, sample_from_each_category: int = 5):
        super().__init__(lambda class_name: f"{config.host_name}:{class_name}")
        self._host_name = config.host_name
        self._processor_to_cache_fifo = config.processor_to_cache_fifo
        self._root_complex = config.root_complex
        self._irq_handler = config.irq_handler
        self._validation_results = []
        self._device_finished_training = 0
        self._sample_from_each_category = sample_from_each_category
        self._sampled_file_categories = []
        self._total_samples: int = 0
        self._correct_validation: int = 0
        self._base_addr = config.base_addr
        self._device_count = config.device_count
        self._interleave_gran = config.interleave_gran
        self._dev_type = config.device_type
        self._cache_controller = config.cache_controller
        self._train_data_path = "/Users/zhxq/Downloads/imagenette2-160"
        self._dev_mmio_ranges: list[tuple[int, int]] = []
        self._dev_mem_ranges: list[tuple[int, int]] = []
        self._start_signal = asyncio.Event()
        self._stop_signal = asyncio.Event()

    def append_dev_mmio_range(self, base, size):
        self._dev_mmio_ranges.append((base, size))

    def append_dev_mem_range(self, base, size):
        self._dev_mem_ranges.append((base, size))

    def set_device_count(self, dev_count: int):
        self._device_count = dev_count

    # pylint: disable=duplicate-code
    async def load(self, address: int, size: int) -> MemoryResponse:
        packet = MemoryRequest(MEMORY_REQUEST_TYPE.READ, address, size)
        await self._processor_to_cache_fifo.request.put(packet)
        packet = await self._processor_to_cache_fifo.response.get()
        return packet

    async def store(self, address: int, size: int, value: int):
        if address % 64 != 0 or size % 64 != 0:
            raise Exception("Size and address must be aligned to 64!")

        chunk_count = 0
        while size > 0:
            message = self._create_message(f"Host Memory: Writing 0x{value:08x} to 0x{address:08x}")
            logger.debug(message)
            low_64_byte = value & ((1 << (64 * 8)) - 1)
            await self._cache_controller.cache_coherent_store(
                address + (chunk_count * 64), 64, low_64_byte
            )
            size -= 64
            chunk_count += 1
            value >>= 64 * 8

    async def write_config(self, bdf: int, offset: int, size: int, value: int):
        await self._root_complex.write_config(bdf, offset, size, value)

    async def read_config(self, bdf: int, offset: int, size: int) -> int:
        return await self._root_complex.read_config(bdf, offset, size)

    async def write_mmio(self, address: int, size: int, value: int):
        await self._root_complex.write_mmio(address, size, value)

    async def read_mmio(self, address: int, size: int) -> int:
        return await self._root_complex.read_mmio(address, size)

    def to_device_mmio_addr(self, device: int, addr: int) -> int:
        assert addr < self._dev_mmio_ranges[device][1]
        return self._dev_mmio_ranges[device][0] + addr

    def to_device_mem_addr(self, device: int, addr: int) -> int:
        assert addr < self._dev_mem_ranges[device][1]
        return self._dev_mem_ranges[device][0] + addr

    async def _host_process_validation_type1(self, reader_id: int):
        print("_host_process_validation_type1 INVOKED!!!!!")
        categories = glob.glob(self._train_data_path + "/val/*")
        self._total_samples = len(categories) * self._sample_from_each_category
        self._validation_results: List[List[Dict[str, float]]] = [[] for _ in self._total_samples]
        pic_id = 0
        pic_data_mem_loc = 0x00008000
        for c in categories:
            category_pics = glob.glob(f"{c}/*.JPEG")
            sample_pics = sample(category_pics, self._sample_from_each_category)
            category_name = c.split(os.path.sep)[-1]
            self._sampled_file_categories += [category_name] * self._sample_from_each_category
            for s in sample_pics:
                with open(s, "rb") as f:
                    pic_data = f.read()
                    pic_data_int = int.from_bytes(pic_data, "little")
                    pic_data_len = len(pic_data)
                    pic_data_len_rounded = (((pic_data_len - 1) // 64) + 1) * 64
                    print(f"loc: 0x{pic_data_mem_loc:x}, len: 0x{pic_data_len_rounded:x}")
                    for dev_id in range(self._device_count):
                        event = asyncio.Event()
                        await self.store(
                            pic_data_mem_loc,
                            pic_data_len_rounded,
                            pic_data_int,
                        )
                    for dev_id in range(self._device_count):
                        # Remember to configure the device when starting the app
                        # Should make sure to_device_addr returns correct mmio for that dev_id
                        # In fact, we can use a fixed host memory addr
                        # and we only need to write the length to the device
                        await self.write_mmio(
                            self.to_device_mmio_addr(dev_id, 0x810), 8, pic_data_mem_loc
                        )
                        await self.write_mmio(
                            self.to_device_mmio_addr(dev_id, 0x818), 8, pic_data_len
                        )
                        event = asyncio.Event()
                        self._irq_handler.register_interrupt_handler(
                            Irq.ACCEL_VALIDATION_FINISHED,
                            self._save_validation_result_type1(dev_id, pic_id, event),
                        )
                        await self._irq_handler.send_irq_request(Irq.HOST_SENT, dev_id)
                        print("send_irq_request done")
                        await event.wait()
                        # Currently we don't send the picture information
                        # (e.g., pic_id) to the device
                        # and to prevent race condition, we need to send pics synchronously
                    pic_data_mem_loc += pic_data_len
                    pic_id += 1
        self._merge_validation_results()

    def _save_validation_result_type1(self, dev_id: int, pic_id: int, event: asyncio.Event):
        async def _func(reader_id: int):
            # We can use a fixed host_result_addr, say 0x0A000000
            # Only length is needed
            host_result_addr = await self.read_mmio(self.to_device_mmio_addr(dev_id, 0x820), 8)
            host_result_len = await self.load(self.to_device_mmio_addr(dev_id, 0x828), 8)
            data = await self.load(host_result_addr, host_result_len)
            data_bytes = data.data.to_bytes(host_result_len, "little")
            validate_result = json.loads(data_bytes)
            self._validation_results[pic_id].append(validate_result)
            event.set()

        return _func

    def _merge_validation_results(self):
        merged_result = {}
        max_v = 0
        max_k = 0
        for pic_id in range(self._total_samples):
            assert len(self._validation_results[pic_id]) == self._device_count
            real_category = self._sampled_file_categories[pic_id]
            for dev_result in self._validation_results[pic_id]:
                for k, v in dev_result.items():
                    if k not in merged_result:
                        merged_result[k] = v
                    else:
                        merged_result[k] += v
                    if merged_result[k] > max_v:
                        max_v = merged_result[k]
                        max_k = k
            if max_k == real_category:
                self._correct_validation += 1

            print(f"Picture category: Real: {real_category}, validated: {max_k}")

        print("Validation finished. Results:")
        print(
            f"Correct/Total: {self._correct_validation}/{self._total_samples} "
            f"({self._correct_validation/self._total_samples:.2f}%)"
        )

    async def _host_process_validation_type2(self):
        categories = glob.glob(self._train_data_path + "/val/*")
        self._total_samples = len(categories) * self._sample_from_each_category
        self._validation_results: List[List[Dict[str, float]]] = [[] for _ in self._total_samples]
        pic_id = 0
        for c in categories:
            category_pics = glob.glob(f"{c}/*.JPEG")
            sample_pics = sample(category_pics, self._sample_from_each_category)
            category_name = c.split(os.path.sep)[-1]
            self._sampled_file_categories += [category_name] * self._sample_from_each_category
            for s in sample_pics:
                with open(s, "rb") as f:
                    pic_data = f.read()
                    pic_data_int = int.from_bytes(pic_data, "little")
                    pic_data_len = len(pic_data)
                    pic_data_len_rounded = (((pic_data_len - 1) // 64) + 1) * 64
                    for dev_id in range(self._device_count):
                        event = asyncio.Event()
                        await self.store(
                            self.to_device_mem_addr(dev_id, 0x00008000),
                            pic_data_len_rounded,
                            pic_data_int,
                        )
                        self._irq_handler.register_interrupt_handler(
                            Irq.ACCEL_VALIDATION_FINISHED,
                            self._save_validation_result_type2(dev_id, pic_id, event),
                        )
                        await self._irq_handler.send_irq_request(Irq.HOST_SENT, dev_id)
                        event.wait()
                        # Currently we don't send the picture information to the device
                        # and to prevent race condition, we need to send pics synchronously
                    pic_id += 1
        self._merge_validation_results()

    def _save_validation_result_type2(self, dev_id: int, pic_id: int, event: asyncio.Event):
        pass

    async def _host_process_llc_iogen(self):
        # Pass init-info mem location to the remote using MMIO
        csv_data_mem_loc = 0x00004000
        csv_data = b""
        print("llc_iogen_waiting to be run")
        await self._start_signal.wait()
        print("llc_iogen_running")
        with open(f"{self._train_data_path}/noisy_imagenette.csv", "rb") as f:
            csv_data = f.read()
        csv_data_int = int.from_bytes(csv_data, "little")
        csv_data_len = len(csv_data)
        csv_data_len_rounded = (((csv_data_len - 1) // 64) + 1) * 64
        print("Storing data...")
        await self.store(csv_data_mem_loc, csv_data_len_rounded, csv_data_int)
        print("Data was stored!")

        for dev_id in range(self._device_count):
            print(f"IRQ_SENT to {dev_id} @ 0x{self.to_device_mmio_addr(dev_id, 0x1800):x}")
            await self.write_mmio(self.to_device_mmio_addr(dev_id, 0x1800), 8, csv_data_mem_loc)
            print(f"IRQ_SENT S2")
            await self.write_mmio(self.to_device_mmio_addr(dev_id, 0x1808), 8, csv_data_len)

        while True:
            csv_data_mem_loc_rb = await self.read_mmio(self.to_device_mmio_addr(dev_id, 0x1800), 8)
            csv_data_len_rb = await self.read_mmio(self.to_device_mmio_addr(dev_id, 0x1808), 8)

            if csv_data_mem_loc_rb == csv_data_mem_loc and csv_data_len_rb == csv_data_len:
                break
            await asyncio.sleep(0.2)

        if self._dev_type == CXL_COMPONENT_TYPE.T1:
            self._irq_handler.register_interrupt_handler(
                Irq.ACCEL_TRAINING_FINISHED, self._host_process_validation_type1
            )
        elif self._dev_type == CXL_COMPONENT_TYPE.T2:
            self._irq_handler.register_interrupt_handler(
                Irq.ACCEL_TRAINING_FINISHED, self._host_process_validation_type2
            )
        else:
            raise Exception("Only T1 and T2 devices are allowed!")

        for dev_id in range(self._device_count):
            await self._irq_handler.send_irq_request(Irq.HOST_READY, dev_id)
            print("IRQ_SENT S4")

        while True:
            await asyncio.sleep(0)
            await self._irq_handler.poll()

    def start_job(self):
        self._start_signal.set()

    async def _run(self):
        tasks = [
            asyncio.create_task(self._host_process_llc_iogen()),
        ]
        await self._change_status_to_running()
        tasks.append(asyncio.create_task(self._stop_signal.wait()))
        await asyncio.gather(*tasks)
        pass

    async def _stop(self):
        await self._processor_to_cache_fifo.response.put(None)


@dataclass
class CxlImageClassificationHostConfig:
    host_name: str
    root_bus: int
    root_port_switch_type: ROOT_PORT_SWITCH_TYPE
    memory_controller: RootComplexMemoryControllerConfig
    memory_ranges: List[MemoryRange] = field(default_factory=list)
    root_ports: List[RootPortClientConfig] = field(default_factory=list)
    coh_type: Optional[COH_POLICY_TYPE] = COH_POLICY_TYPE.DotMemBI


class CxlImageClassificationHost(RunnableComponent):
    def __init__(self, config: CxlImageClassificationHostConfig):
        super().__init__(lambda class_name: f"{config.host_name}:{class_name}")

        processor_to_cache_fifo = MemoryFifoPair()
        processor_to_mem_fifo = MemoryFifoPair()
        cache_to_home_agent_fifo = CacheFifoPair()
        home_agent_to_cache_fifo = CacheFifoPair()
        cache_to_coh_bridge_fifo = CacheFifoPair()
        coh_bridge_to_cache_fifo = CacheFifoPair()

        self._irq_handler = IrqManager(
            device_name=config.host_name, addr="127.0.0.1", port=9050, server=True
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
            memory_ranges=config.memory_ranges,
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
            processor_to_cache_fifo=processor_to_cache_fifo,
            root_complex=self._root_complex,
            irq_handler=self._irq_handler,
            base_addr=0x290000000,
            device_count=1,
            interleave_gran=0x100,
            device_type=CXL_COMPONENT_TYPE.T1,
            cache_controller=self._cache_controller,
        )
        self._host_simple_processor = HostTrainIoGen(host_processor_config)

    def get_root_complex(self):
        return self._root_complex

    def append_dev_mmio_range(self, base, size):
        self._host_simple_processor.append_dev_mmio_range(base, size)

    def append_dev_mem_range(self, base, size):
        self._host_simple_processor.append_dev_mem_range(base, size)

    def start_job(self):
        self._host_simple_processor.start_job()

    def set_device_count(self, dev_count: int):
        self._host_simple_processor.set_device_count(dev_count)

    async def _run(self):
        run_tasks = [
            asyncio.create_task(self._irq_handler.run()),
            asyncio.create_task(self._root_port_client_manager.run()),
            asyncio.create_task(self._root_complex.run()),
            asyncio.create_task(self._cache_controller.run()),
            asyncio.create_task(self._host_simple_processor.run()),
        ]
        wait_tasks = [
            asyncio.create_task(self._irq_handler.wait_for_ready()),
            asyncio.create_task(self._root_port_client_manager.wait_for_ready()),
            asyncio.create_task(self._root_complex.wait_for_ready()),
            asyncio.create_task(self._cache_controller.wait_for_ready()),
            asyncio.create_task(self._host_simple_processor.wait_for_ready()),
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
