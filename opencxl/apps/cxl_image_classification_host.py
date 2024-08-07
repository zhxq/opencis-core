"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

# pylint: disable=duplicate-code, unused-import, unused-argument
import asyncio
import glob
import os
import json
from dataclasses import dataclass, field
from typing import Dict, Optional, List
from random import sample
from tqdm.auto import tqdm, trange

from opencxl.cxl.component.common import CXL_COMPONENT_TYPE
from opencxl.util.logger import logger
from opencxl.cxl.transport.memory_fifo import (
    MemoryFifoPair,
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
    interleave_gran: int
    device_type: CXL_COMPONENT_TYPE.T1
    cache_controller: CacheController
    train_data_path: str
    device_count: Optional[int] = None


class HostTrainIoGen(RunnableComponent):
    def __init__(self, config: HostTrainIoGenConfig, sample_from_each_category: int = 2):
        super().__init__(lambda class_name: f"{config.host_name}:{class_name}")
        self._host_name = config.host_name
        self._processor_to_cache_fifo = config.processor_to_cache_fifo
        self._root_complex = config.root_complex
        self._irq_handler = config.irq_handler
        self._validation_results: List[List[Dict[str, float]]] = []
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
        self._train_data_path = config.train_data_path
        self._dev_mmio_ranges: list[tuple[int, int]] = []
        self._dev_mem_ranges: list[tuple[int, int]] = []
        self._start_signal = asyncio.Event()
        self._stop_signal = asyncio.Event()
        self._internal_stop_signal = asyncio.Event()
        self._train_finished_count = 0
        self._validation_results_lock = asyncio.Lock()

    def append_dev_mmio_range(self, base, size):
        self._dev_mmio_ranges.append((base, size))

    def append_dev_mem_range(self, base, size):
        self._dev_mem_ranges.append((base, size))

    def set_device_count(self, dev_count: int):
        self._device_count = dev_count

    # pylint: disable=duplicate-code
    async def load(self, address: int, size: int, prog_bar: bool = False) -> bytes:
        end = address + size
        result = b""
        with tqdm(
            total=size,
            desc="Reading Data",
            unit="iB",
            unit_scale=True,
            unit_divisor=1024,
            disable=not prog_bar,
        ) as pbar:
            for cacheline_offset in range(address, address + size, 64):
                cacheline = await self._cache_controller.cache_coherent_load(cacheline_offset, 64)
                chunk_size = min(64, (end - cacheline_offset))
                chunk_data = cacheline.to_bytes(64, "little")
                result += chunk_data[:chunk_size]
                pbar.update(chunk_size)
        return result

    async def store(self, address: int, size: int, value: int, prog_bar: bool = False):
        if address % 64 != 0 or size % 64 != 0:
            raise Exception("Size and address must be aligned to 64!")

        with tqdm(
            total=size,
            desc="Writing Data",
            unit="iB",
            unit_scale=True,
            unit_divisor=1024,
            disable=not prog_bar,
        ) as pbar:
            chunk_count = 0
            while size > 0:
                low_64_byte = value & ((1 << (64 * 8)) - 1)
                await self._cache_controller.cache_coherent_store(
                    address + (chunk_count * 64), 64, low_64_byte
                )
                size -= 64
                chunk_count += 1
                value >>= 64 * 8
                pbar.update(64)

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

    async def _host_check_start_validation_type1(self, dev_id: int):
        self._train_finished_count += 1
        if self._train_finished_count == self._device_count:
            await self._host_process_validation_type1(dev_id)

    async def _host_process_validation_type1(self, _: int):
        categories = glob.glob(self._train_data_path + "/val/*")
        self._total_samples = len(categories) * self._sample_from_each_category
        self._validation_results = [[] for _ in range(self._total_samples)]
        pic_id = 0
        pic_data_mem_loc = 0x00008000
        logger.info(
            f"Validation process started. Total pictures: {self._total_samples}, "
            f"Num. of Accelerators: {self._device_count}"
        )
        with tqdm(total=self._total_samples, desc="Picture", position=0) as pbar_cat:
            for c in categories:
                logger.debug(self._create_message(f"Validating category: {c}"))
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
                        logger.debug(
                            self._create_message(
                                f"Reading loc: 0x{pic_data_mem_loc:x}"
                                f"len: 0x{pic_data_len_rounded:x}",
                            )
                        )
                        await self.store(
                            pic_data_mem_loc,
                            pic_data_len_rounded,
                            pic_data_int,
                        )
                        pic_events: list[asyncio.Event] = []
                        for dev_id in tqdm(
                            range(self._device_count),
                            desc="Device Progress",
                            position=1,
                            leave=False,
                        ):
                            event = asyncio.Event()
                            pic_events.append(event)
                            self._irq_handler.register_interrupt_handler(
                                Irq.ACCEL_VALIDATION_FINISHED,
                                self._save_validation_result_type1(pic_id, event),
                                dev_id,
                            )
                            await self.write_mmio(
                                self.to_device_mmio_addr(dev_id, 0x1810), 8, pic_data_mem_loc
                            )
                            await self.write_mmio(
                                self.to_device_mmio_addr(dev_id, 0x1818), 8, pic_data_len
                            )
                            while True:
                                pic_data_mem_loc_rb = await self.read_mmio(
                                    self.to_device_mmio_addr(dev_id, 0x1810), 8
                                )
                                pic_data_len_rb = await self.read_mmio(
                                    self.to_device_mmio_addr(dev_id, 0x1818), 8
                                )

                                if (
                                    pic_data_mem_loc_rb == pic_data_mem_loc
                                    and pic_data_len_rb == pic_data_len
                                ):
                                    break
                                await asyncio.sleep(0.2)
                            await self._irq_handler.send_irq_request(Irq.HOST_SENT, dev_id)
                            await event.wait()
                        pic_data_mem_loc += pic_data_len
                        pic_data_mem_loc = (((pic_data_mem_loc - 1) // 64) + 1) * 64
                        pic_id += 1
                        pbar_cat.update(1)
        self._merge_validation_results()

    def _save_validation_result_type1(self, pic_id: int, event: asyncio.Event):
        async def _func(dev_id: int):
            logger.debug(f"Saving validation results pic: {pic_id}, dev: {dev_id}")
            host_result_addr = await self.read_mmio(self.to_device_mmio_addr(dev_id, 0x1820), 8)
            host_result_len = await self.read_mmio(self.to_device_mmio_addr(dev_id, 0x1828), 8)
            data_bytes = await self.load(host_result_addr, host_result_len)
            validate_result = json.loads(data_bytes.decode())
            self._validation_results[pic_id].append(validate_result)
            event.set()

        return _func

    def _merge_validation_results(self):
        for pic_id in range(self._total_samples):
            merged_result = {}
            max_v = 0
            max_k = 0
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

            print(f"Picture {pic_id} category: Real: {real_category}, validated: {max_k}")

        print("Validation finished. Results:")
        print(
            f"Correct/Total: {self._correct_validation}/{self._total_samples} "
            f"({100 * self._correct_validation/self._total_samples:.2f}%)"
        )
        self._stop_signal.set()

    async def _host_check_start_validation_type2(self, dev_id: int):
        self._train_finished_count += 1
        if self._train_finished_count == self._device_count:
            await self._host_process_validation_type2(dev_id)

    async def _host_process_validation_type2(self, _):
        categories = glob.glob(self._train_data_path + "/val/*")
        self._total_samples = len(categories) * self._sample_from_each_category
        self._validation_results = [[] for i in range(self._total_samples)]
        pic_id = 0
        IMAGE_WRITE_ADDR = 0x8000
        with tqdm(total=self._total_samples, desc="Picture", position=0) as pbar_cat:
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
                        for dev_id in tqdm(
                            range(self._device_count),
                            desc="Device Progress",
                            position=1,
                            leave=False,
                        ):
                            event = asyncio.Event()
                            write_addr = self.to_device_mem_addr(dev_id, IMAGE_WRITE_ADDR)
                            await self.store(
                                write_addr,
                                pic_data_len_rounded,
                                pic_data_int,
                            )
                            await self.write_mmio(
                                self.to_device_mmio_addr(dev_id, 0x1810), 8, IMAGE_WRITE_ADDR
                            )
                            await self.write_mmio(
                                self.to_device_mmio_addr(dev_id, 0x1818), 8, pic_data_len
                            )
                            while True:
                                pic_data_mem_loc_rb = await self.read_mmio(
                                    self.to_device_mmio_addr(dev_id, 0x1810), 8
                                )
                                pic_data_len_rb = await self.read_mmio(
                                    self.to_device_mmio_addr(dev_id, 0x1818), 8
                                )

                                if (
                                    pic_data_mem_loc_rb == IMAGE_WRITE_ADDR
                                    and pic_data_len_rb == pic_data_len
                                ):
                                    break
                                await asyncio.sleep(0.2)
                            self._irq_handler.register_interrupt_handler(
                                Irq.ACCEL_VALIDATION_FINISHED,
                                self._save_validation_result_type2(dev_id, pic_id, event),
                                dev_id,
                            )
                            await self._irq_handler.send_irq_request(Irq.HOST_SENT, dev_id)
                            await event.wait()
                            # Currently we don't send the picture information to the device
                            # and to prevent race condition, we need to send pics synchronously
                        pic_id += 1
                        pbar_cat.update(1)
        self._merge_validation_results()

    def _save_validation_result_type2(self, dev_id: int, pic_id: int, event: asyncio.Event):
        async def _func(dev_id: int):
            logger.debug(
                self._create_message(
                    f"Saving validation results for pic: {pic_id} from dev: {dev_id}"
                )
            )
            host_result_addr = await self.read_mmio(self.to_device_mmio_addr(dev_id, 0x1820), 8)
            host_result_len = await self.read_mmio(self.to_device_mmio_addr(dev_id, 0x1828), 8)
            host_result_addr = self.to_device_mem_addr(dev_id, host_result_addr)
            data_bytes = await self.load(host_result_addr, host_result_len)
            validate_result = json.loads(data_bytes.decode())
            self._validation_results[pic_id].append(validate_result)
            event.set()

        return _func

    async def _host_process_llc_iogen(self):
        # Pass init-info mem location to the remote using MMIO
        csv_data_mem_loc = 0x00004000
        csv_data = b""
        logger.info(self._create_message("Host main process waiting..."))
        await self._start_signal.wait()
        logger.info(self._create_message("Host main process running!"))
        with open(f"{self._train_data_path}/noisy_imagenette.csv", "rb") as f:
            csv_data = f.read()
        csv_data_int = int.from_bytes(csv_data, "little")
        csv_data_len = len(csv_data)
        csv_data_len_rounded = (((csv_data_len - 1) // 64) + 1) * 64

        logger.info(self._create_message("Storing metadata..."))
        if self._dev_type == CXL_COMPONENT_TYPE.T1:
            await self.store(csv_data_mem_loc, csv_data_len_rounded, csv_data_int, prog_bar=True)
            for dev_id in range(self._device_count):
                await self.write_mmio(self.to_device_mmio_addr(dev_id, 0x1800), 8, csv_data_mem_loc)
                await self.write_mmio(self.to_device_mmio_addr(dev_id, 0x1808), 8, csv_data_len)

                while True:
                    csv_data_mem_loc_rb = await self.read_mmio(
                        self.to_device_mmio_addr(dev_id, 0x1800), 8
                    )
                    csv_data_len_rb = await self.read_mmio(
                        self.to_device_mmio_addr(dev_id, 0x1808), 8
                    )

                    if csv_data_mem_loc_rb == csv_data_mem_loc and csv_data_len_rb == csv_data_len:
                        break
                    await asyncio.sleep(0.2)
                self._irq_handler.register_interrupt_handler(
                    Irq.ACCEL_TRAINING_FINISHED, self._host_check_start_validation_type1, dev_id
                )
        elif self._dev_type == CXL_COMPONENT_TYPE.T2:
            for dev_id in range(self._device_count):
                write_addr = csv_data_mem_loc + self.to_device_mem_addr(dev_id, csv_data_mem_loc)
                await self.store(write_addr, csv_data_len_rounded, csv_data_int, prog_bar=True)
                await self.write_mmio(self.to_device_mmio_addr(dev_id, 0x1800), 8, write_addr)
                await self.write_mmio(self.to_device_mmio_addr(dev_id, 0x1808), 8, csv_data_len)

                while True:
                    csv_data_mem_loc_rb = await self.read_mmio(
                        self.to_device_mmio_addr(dev_id, 0x1800), 8
                    )
                    csv_data_len_rb = await self.read_mmio(
                        self.to_device_mmio_addr(dev_id, 0x1808), 8
                    )

                    if csv_data_mem_loc_rb == write_addr and csv_data_len_rb == csv_data_len:
                        break
                    await asyncio.sleep(0.2)
                self._irq_handler.register_interrupt_handler(
                    Irq.ACCEL_TRAINING_FINISHED, self._host_check_start_validation_type2, dev_id
                )
        else:
            raise Exception("Only T1 and T2 devices are allowed!")

        for dev_id in range(self._device_count):
            logger.info(f"Sending HOST_READY to dev {dev_id}")
            await self._irq_handler.send_irq_request(Irq.HOST_READY, dev_id)
            logger.info(f"Finished sending HOST_READY to dev {dev_id}")

        await self._internal_stop_signal.wait()

    async def start_job(self):
        self._start_signal.set()
        await self._stop_signal.wait()

    async def _run(self):
        tasks = [
            asyncio.create_task(self._host_process_llc_iogen()),
        ]
        await self._change_status_to_running()
        tasks.append(asyncio.create_task(self._stop_signal.wait()))
        await asyncio.gather(*tasks)

    async def _stop(self):
        self._internal_stop_signal.set()
        await self._irq_handler.shutdown()
        await self._processor_to_cache_fifo.response.put(None)


@dataclass
class CxlImageClassificationHostConfig:
    host_name: str
    root_bus: int
    root_port_switch_type: ROOT_PORT_SWITCH_TYPE
    train_data_path: str
    memory_controller: RootComplexMemoryControllerConfig
    memory_ranges: List[MemoryRange] = field(default_factory=list)
    root_ports: List[RootPortClientConfig] = field(default_factory=list)
    coh_type: Optional[COH_POLICY_TYPE] = COH_POLICY_TYPE.DotMemBI
    device_type: Optional[CXL_COMPONENT_TYPE] = None
    base_addr: int = 0x0


class CxlImageClassificationHost(RunnableComponent):
    def __init__(self, config: CxlImageClassificationHostConfig):
        super().__init__(lambda class_name: f"{config.host_name}:{class_name}")

        processor_to_cache_fifo = MemoryFifoPair()
        cache_to_home_agent_fifo = CacheFifoPair()
        home_agent_to_cache_fifo = CacheFifoPair()
        cache_to_coh_bridge_fifo = CacheFifoPair()
        coh_bridge_to_cache_fifo = CacheFifoPair()

        if not os.path.exists(config.train_data_path) or not os.path.isdir(config.train_data_path):
            raise Exception(f"Path {config.train_data_path} does not exist, or is not a folder.")

        self._irq_handler = IrqManager(
            device_name=config.host_name, addr="127.0.0.1", port=9050, server=True, device_id=9
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
            coh_type=config.coh_type,
        )
        self._root_complex = RootComplex(root_complex_config)

        if config.coh_type == COH_POLICY_TYPE.DotCache:
            cache_to_coh_agent_fifo = cache_to_coh_bridge_fifo
            coh_agent_to_cache_fifo = coh_bridge_to_cache_fifo
        elif config.coh_type in (COH_POLICY_TYPE.NonCache, COH_POLICY_TYPE.DotMemBI):
            cache_to_coh_agent_fifo = cache_to_home_agent_fifo
            coh_agent_to_cache_fifo = home_agent_to_cache_fifo

        cache_controller_config = CacheControllerConfig(
            component_name=config.host_name,
            processor_to_cache_fifo=processor_to_cache_fifo,
            cache_to_coh_agent_fifo=cache_to_coh_agent_fifo,
            coh_agent_to_cache_fifo=coh_agent_to_cache_fifo,
            cache_num_assoc=4,  ### used to be 4
            cache_num_set=8,
        )
        self._cache_controller = CacheController(cache_controller_config)

        if config.device_type is None:
            dev_type = CXL_COMPONENT_TYPE.T1
        else:
            dev_type = config.device_type

        host_processor_config = HostTrainIoGenConfig(
            host_name=config.host_name,
            processor_to_cache_fifo=processor_to_cache_fifo,
            root_complex=self._root_complex,
            irq_handler=self._irq_handler,
            base_addr=config.base_addr,
            device_count=1,
            interleave_gran=0x100,
            device_type=dev_type,
            cache_controller=self._cache_controller,
            train_data_path=config.train_data_path,
        )
        self._host_simple_processor = HostTrainIoGen(host_processor_config)

    def get_root_complex(self):
        return self._root_complex

    def append_dev_mmio_range(self, base, size):
        self._host_simple_processor.append_dev_mmio_range(base, size)

    def append_dev_mem_range(self, base, size):
        self._host_simple_processor.append_dev_mem_range(base, size)

    async def start_job(self):
        await self._host_simple_processor.start_job()

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
