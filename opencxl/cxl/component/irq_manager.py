"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from asyncio import (
    Event,
    StreamReader,
    StreamWriter,
    Task,
    create_task,
    gather,
    sleep,
    start_server,
    open_connection,
    Lock,
)
from asyncio.exceptions import CancelledError
from enum import Enum
import traceback
from typing import Callable, Optional

from opencxl.util.component import RunnableComponent
from opencxl.util.logger import logger


class Irq(Enum):
    NULL = 0x00

    # Host-side file ready to be read by device using CXL.cache
    HOST_READY = 0x01

    # Device-side results ready to be read by host using CXL.mem
    ACCEL_VALIDATION_FINISHED = 0x02

    # Host finished writing file to device via CXL.mem
    HOST_SENT = 0x03

    # Accelerator finished training, waiting for host to send validation pics
    ACCEL_TRAINING_FINISHED = 0x04


IRQ_WIDTH = 1  # in bytes


class IrqManager(RunnableComponent):
    _msg_to_interrupt_event: dict[Irq, Callable]
    _callbacks: list[Callable]
    _server_task: Task
    _callback_tasks: list[Task]

    def __init__(
        self,
        device_name,
        addr: str = "0.0.0.0",
        port: int = 9050,
        server: bool = False,
    ):
        super().__init__(f"{device_name}:IrqHandler")
        self._addr = addr
        self._port = port
        self._callbacks = []
        self._msg_to_interrupt_event = {}
        self._server = server
        self._connections: list[tuple[StreamReader, StreamWriter]] = []
        self._tasks: list[Task] = []
        self._callback_tasks = []
        self._new_irq_tasks = []
        self._lock = Lock()
        self._end_signal = Event()
        self._reader_id = {}
        self._writer_id = {}

    def register_interrupt_handler(self, irq_msg: Irq, irq_recv_cb: Callable):
        """
        Registers a callback on the arrival of a specific interrupt.
        Cannot be done while IrqManager is running.
        """

        async def _callback(dev_id):
            await irq_recv_cb(dev_id)

        cb_func = _callback

        print(f"Registering interrupt for IRQ {irq_msg.name}")

        self._msg_to_interrupt_event[irq_msg] = cb_func

    async def _irq_handler(self, reader: StreamReader, writer: StreamWriter):
        while True:
            if not self._run_status:
                print("_irq_handler exiting")
                return
            msg = await reader.readexactly(1)
            if not msg:
                logger.debug(self._create_message("Irq enable connection broken"))
                return
            print(msg)
            irq = Irq(int.from_bytes(msg))
            print(f"IRQ received for {irq.name}")
            if irq not in self._msg_to_interrupt_event:
                raise RuntimeError(f"Invalid IRQ: {irq}")

            await self._msg_to_interrupt_event[irq](id(reader))
            print(f"IRQ handled for {irq.name}")

    async def poll(self):
        # print("Polling")
        await sleep(0)

    async def _create_server(self):
        async def _new_conn(reader: StreamReader, writer: StreamWriter):
            self._connections.append((reader, writer))
            conn_idx = len(self._connections) - 1
            self._reader_id[id(reader)] = conn_idx
            self._writer_id[id(writer)] = conn_idx

        server = await start_server(_new_conn, self._addr, self._port, limit=1)
        print(f"Starting server on {self._addr}:{self._port}")
        return server

    async def send_irq_request(self, request: Irq, device: int = 0):
        """
        Sends an IRQ request as the client.
        """
        print(f"Sending to device {device}")
        reader, writer = self._connections[device]
        writer.write(request.value.to_bytes(length=IRQ_WIDTH))
        await writer.drain()

    async def start_connection(self):
        print("Device to Host IRQ Connection started!")
        reader, writer = await open_connection(self._addr, self._port, limit=1)
        self._connections.append((reader, writer))
        print("Device to Host IRQ Connection created!")
        self._run_status = True
        t = create_task(self._irq_handler(reader, writer))
        return t

    async def _run(self):
        try:
            if self._server:
                server = await self._create_server()
                self._server_task = create_task(server.serve_forever())

                self._tasks.append(self._server_task)
                # self._tasks.append(self._handler_task)
            else:
                # self._client_task = create_task(self._irq_handler())
                # self._tasks.append(self._client_task)
                pass
            await self._change_status_to_running()
            self._tasks.append(create_task(await self._end_signal.wait()))
            while True:
                await sleep(0)
            await gather(*self._tasks)
            # await gather(*self._callback_tasks)
        except CancelledError:
            logger.info(self._create_message("Irq enable listener stopped"))

    async def _stop(self):
        print("IRQ Manager Stopping")
        for callback_task in self._callback_tasks:
            callback_task.cancel()
        for task in self._tasks:
            task.cancel()
