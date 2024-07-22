"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from typing import Optional, Tuple
from asyncio import create_task, gather
from dataclasses import dataclass
from enum import Enum, auto
from math import log2

from opencxl.util.component import RunnableComponent
from opencxl.cxl.transport.memory_fifo import (
    MemoryFifoPair,
    MEMORY_REQUEST_TYPE,
    MemoryResponse,
    MEMORY_RESPONSE_STATUS,
)
from opencxl.cxl.transport.cache_fifo import (
    CacheFifoPair,
    CacheRequest,
    CACHE_REQUEST_TYPE,
    CacheResponse,
    CACHE_RESPONSE_STATUS,
)
from opencxl.util.logger import logger


class CacheCheck(Enum):
    CACHE_HIT = auto()
    CACHE_MISS = auto()


class CacheState(Enum):
    CACHE_INVALID = auto()
    CACHE_SHARED = auto()
    CACHE_EXCLUSIVE = auto()
    CACHE_MODIFIED = auto()


@dataclass
class CacheBlock:
    state: CacheState = CacheState.CACHE_INVALID
    tag: int = 0
    priority: int = 0
    data: int = 0


@dataclass
class SetCounter:
    counter: int = 0


@dataclass
class CacheControllerConfig:
    component_name: str
    processor_to_cache_fifo: MemoryFifoPair
    cache_to_coh_agent_fifo: CacheFifoPair
    coh_agent_to_cache_fifo: CacheFifoPair
    cache_num_assoc: Optional[int] = 4
    cache_num_set: Optional[int] = 8


class CacheController(RunnableComponent):
    def __init__(self, config: CacheControllerConfig):
        self._component_name = config.component_name
        super().__init__(lambda class_name: f"{config.component_name}:{class_name}")

        self._cache_blk_bit = 6
        self._cache_blk_size = 1 << self._cache_blk_bit

        self._cache_assoc_size = config.cache_num_assoc
        self._cache_assoc_bit = int(log2(self._cache_assoc_size))
        assert self._cache_assoc_size == (1 << self._cache_assoc_bit)

        self._cache_set_size = config.cache_num_set
        self._cache_set_bit = int(log2(self._cache_set_size))
        assert self._cache_set_size == (1 << self._cache_set_bit)

        # cache controller connections within CXL complex host module
        self._processor_to_cache_fifo = config.processor_to_cache_fifo
        self._cache_to_coh_agent_fifo = config.cache_to_coh_agent_fifo
        self._coh_agent_to_cache_fifo = config.coh_agent_to_cache_fifo

        self._init_cache()
        logger.info(self._create_message(f"{config.component_name} LLC Generated"))

    def _init_cache(self) -> None:
        # simple cache structure
        self._cache = [
            [CacheBlock() for assoc in range(self._cache_assoc_size)]
            for set in range(self._cache_set_size)
        ]
        # for cache block eviction algorithm
        self._setcnt = [SetCounter() for set in range(self._cache_set_size)]

        self._blk_mask = self._cache_blk_size - 1
        self._set_mask = (self._cache_set_size - 1) << self._cache_blk_bit
        self._tag_mask = ~(self._set_mask | self._blk_mask)

    def _cache_priority_update(self, set: int, blk: int) -> None:
        self._cache[set][blk].priority = self._setcnt[set].counter
        self._setcnt[set].counter += 1

    def _cache_extract_tag(self, addr: int) -> int:
        return (addr & self._tag_mask) >> (self._cache_set_bit + self._cache_blk_bit)

    def _cache_extract_set(self, addr: int) -> int:
        return (addr & self._set_mask) >> self._cache_blk_bit

    def _cache_extract_block_state(self, set: int, blk: int) -> CacheState:
        return self._cache[set][blk].state

    def _cache_assem_addr(self, set: int, blk: int) -> int:
        tag = self._cache[set][blk].tag
        assert self._cache[set][blk].state != CacheState.CACHE_INVALID

        return tag << (self._cache_set_bit + self._cache_blk_bit) | set << self._cache_blk_bit

    def _cache_update_block_state(self, tag: int, set: int, blk: int, state: CacheState) -> None:
        if state != CacheState.CACHE_INVALID:
            self._cache_priority_update(set, blk)

        self._cache[set][blk].tag = tag
        self._cache[set][blk].state = state

    def _cache_find_replace_block(self, set: int) -> int:
        min_priority = self._cache[set][0].priority
        min_idx = 0

        for idx in range(1, self._cache_assoc_size):
            if self._cache[set][idx].priority < min_priority:
                min_priority = self._cache[set][idx].priority
                min_idx = idx

        return min_idx

    def _cache_find_invalid_block(self, set: int) -> int:
        for blk in range(self._cache_assoc_size):
            if self._cache[set][blk].state == CacheState.CACHE_INVALID:
                return blk

        return None

    def _cache_find_valid_block(self, tag: int, set: int) -> int:
        for blk in range(self._cache_assoc_size):
            if (self._cache[set][blk].tag == tag) and (
                self._cache[set][blk].state != CacheState.CACHE_INVALID
            ):
                return blk

        return None

    def _cache_data_read(self, set: int, blk: int) -> int:
        self._cache_priority_update(set, blk)

        return self._cache[set][blk].data

    def _cache_data_write(self, set: int, blk: int, data: int) -> None:
        self._cache_priority_update(set, blk)
        self._cache[set][blk].data = data

    def _cache_rsp_state_lookup(self, packet: CacheResponse) -> CacheState:
        if packet.status == CACHE_RESPONSE_STATUS.OK:
            cache_state = CacheState.CACHE_EXCLUSIVE
        elif packet.status == CACHE_RESPONSE_STATUS.RSP_S:
            cache_state = CacheState.CACHE_SHARED
        elif packet.status == CACHE_RESPONSE_STATUS.RSP_I:
            cache_state = CacheState.CACHE_EXCLUSIVE
        elif packet.status == CACHE_RESPONSE_STATUS.RSP_V:
            cache_state = CacheState.CACHE_INVALID
        return cache_state

    async def _memory_load(self, address: int, size: int) -> CacheResponse:
        packet = CacheRequest(CACHE_REQUEST_TYPE.SNP_DATA, address, size)
        await self._cache_to_coh_agent_fifo.request.put(packet)
        packet = await self._cache_to_coh_agent_fifo.response.get()

        return packet

    async def _memory_store(self, address: int, size: int, value: int) -> None:
        packet = CacheRequest(CACHE_REQUEST_TYPE.WRITE_BACK, address, size, value)
        await self._cache_to_coh_agent_fifo.request.put(packet)

    # For request: coherency tasks from cache controller to coh module
    async def _cache_to_coh_state_lookup(self, address: int) -> None:
        packet = CacheRequest(CACHE_REQUEST_TYPE.SNP_INV, address)
        print(packet)
        await self._cache_to_coh_agent_fifo.request.put(packet)
        print("getting")
        # NOTE: to self: the process stuck at the next line
        packet = await self._cache_to_coh_agent_fifo.response.get()
        print("Is it here?")
        assert packet.status == CACHE_RESPONSE_STATUS.RSP_I

    # For response: coherency tasks from coh module to cache controller
    async def _coh_to_cache_state_lookup(
        self, type: CACHE_REQUEST_TYPE, addr: int
    ) -> Tuple[int, int]:
        data = 0
        tag = self._cache_extract_tag(addr)
        set = self._cache_extract_set(addr)

        cache_blk = self._cache_find_valid_block(tag, set)

        if cache_blk is not None:
            data = self._cache_data_read(set, cache_blk)
            if type == CACHE_REQUEST_TYPE.SNP_DATA:
                self._cache_update_block_state(tag, set, cache_blk, CacheState.CACHE_SHARED)
            elif type == CACHE_REQUEST_TYPE.SNP_INV:
                self._cache_update_block_state(tag, set, cache_blk, CacheState.CACHE_INVALID)
            elif type == CACHE_REQUEST_TYPE.SNP_CUR:
                pass
            elif type == CACHE_REQUEST_TYPE.WRITE_BACK:
                assert self._cache_extract_block_state(set, cache_blk) == CacheState.CACHE_SHARED

        return cache_blk, data

    # cache access for read
    async def cache_coherent_load(self, addr: int, size: int) -> int:
        assert size == self._cache_blk_size

        tag = self._cache_extract_tag(addr)
        set = self._cache_extract_set(addr)

        cache_blk = self._cache_find_valid_block(tag, set)

        # cache hit
        if cache_blk is not None:
            data = self._cache_data_read(set, cache_blk)
        # cache miss
        else:
            cache_blk = self._cache_find_invalid_block(set)

            # cache block full
            if cache_blk is None:
                cache_blk = self._cache_find_replace_block(set)
                assem_addr = self._cache_assem_addr(set, cache_blk)
                cached_data = self._cache_data_read(set, cache_blk)

                # cacheline flush to secure space
                await self._memory_store(assem_addr, size, cached_data)
                self._cache_update_block_state(tag, set, cache_blk, CacheState.CACHE_INVALID)

            # coherency check whenever inserting a cache block
            # snoop_data to get mesi response
            packet = await self._memory_load(addr, size)
            data = packet.data

            cache_state = self._cache_rsp_state_lookup(packet)
            if cache_state == CacheState.CACHE_INVALID:
                return data

            assert self._cache_extract_block_state(set, cache_blk) == CacheState.CACHE_INVALID
            self._cache_update_block_state(tag, set, cache_blk, cache_state)
            self._cache_data_write(set, cache_blk, data)

        return data

    # cache access for write
    async def cache_coherent_store(self, addr: int, size: int, data: int) -> None:
        assert size == self._cache_blk_size

        tag = self._cache_extract_tag(addr)
        set = self._cache_extract_set(addr)

        cache_blk = self._cache_find_valid_block(tag, set)

        # cache hit
        if cache_blk is not None:
            cache_state = self._cache_extract_block_state(set, cache_blk)
            assert cache_state != CacheState.CACHE_INVALID

            if cache_state == CacheState.CACHE_SHARED:
                # can be real shared or exclusive-shared
                await self._cache_to_coh_state_lookup(addr)
                self._cache_update_block_state(tag, set, cache_blk, CacheState.CACHE_EXCLUSIVE)

            self._cache_update_block_state(tag, set, cache_blk, CacheState.CACHE_MODIFIED)
            self._cache_data_write(set, cache_blk, data)
        # cache miss
        else:
            print("cache_controller 1")
            cache_blk = self._cache_find_invalid_block(set)

            # cache block full
            if cache_blk is None:
                print("cache_controller 2")
                cache_blk = self._cache_find_replace_block(set)
                assem_addr = self._cache_assem_addr(set, cache_blk)
                cached_data = self._cache_data_read(set, cache_blk)
                print("cache_controller 3")
                # cacheline flush to secure space
                await self._memory_store(assem_addr, size, cached_data)
                self._cache_update_block_state(tag, set, cache_blk, CacheState.CACHE_INVALID)

            # coherency check whenever inserting a cache block
            # always snoop_invalidate for now
            print("cache_controller 4")
            await self._cache_to_coh_state_lookup(addr)

            # todo: read memory if partial update is supported
            print("cache_controller 5")
            self._cache_update_block_state(tag, set, cache_blk, CacheState.CACHE_MODIFIED)
            print("cache_controller 6")
            self._cache_data_write(set, cache_blk, data)

    # registered event loop for processor's cache load/store operations
    async def _processor_request_scheduler(self):
        while True:
            packet = await self._processor_to_cache_fifo.request.get()
            if packet is None:
                logger.debug(
                    self._create_message("Stop processing processor request scheduler fifo")
                )
                break

            if packet.type == MEMORY_REQUEST_TYPE.READ:
                data = await self.cache_coherent_load(packet.address, packet.size)
                packet = MemoryResponse(MEMORY_RESPONSE_STATUS.OK, data)
                await self._processor_to_cache_fifo.response.put(packet)
            elif packet.type == MEMORY_REQUEST_TYPE.WRITE:
                await self.cache_coherent_store(packet.address, packet.size, packet.data)
                packet = MemoryResponse(MEMORY_RESPONSE_STATUS.OK)
                await self._processor_to_cache_fifo.response.put(packet)
            else:
                assert False

    # registered event loop for coh module's cache loopup operations
    async def _coh_request_scheduler(self):
        while True:
            packet = await self._coh_agent_to_cache_fifo.request.get()
            if packet is None:
                logger.debug(self._create_message("Stop processing coh request scheduler fifo"))
                break

            cache_blk, data = await self._coh_to_cache_state_lookup(packet.type, packet.address)
            if cache_blk is None:
                packet = CacheResponse(CACHE_RESPONSE_STATUS.RSP_MISS, data)
            elif packet.type == CACHE_REQUEST_TYPE.SNP_DATA:
                packet = CacheResponse(CACHE_RESPONSE_STATUS.RSP_S, data)
            elif packet.type == CACHE_REQUEST_TYPE.SNP_INV:
                packet = CacheResponse(CACHE_RESPONSE_STATUS.RSP_I, data)
            elif packet.type == CACHE_REQUEST_TYPE.SNP_CUR:
                packet = CacheResponse(CACHE_RESPONSE_STATUS.RSP_V, data)
            elif packet.type == CACHE_REQUEST_TYPE.WRITE_BACK:
                packet = CacheResponse(CACHE_RESPONSE_STATUS.RSP_V, data)
            else:
                assert False
            await self._coh_agent_to_cache_fifo.response.put(packet)

    async def _run(self):
        tasks = [
            create_task(self._processor_request_scheduler()),
            create_task(self._coh_request_scheduler()),
        ]
        await self._change_status_to_running()
        await gather(*tasks)

    async def _stop(self):
        await self._processor_to_cache_fifo.request.put(None)
        await self._coh_agent_to_cache_fifo.request.put(None)
