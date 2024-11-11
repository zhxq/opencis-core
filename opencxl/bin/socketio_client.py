"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

import socketio
import asyncio
import sys
from yaml import dump

# Standard Python client setup for Socket.IO
sio = socketio.AsyncClient()


@sio.on("port:updated")
def handle_port_updated():
    print("[Notification]")
    print("port:updated")


@sio.on("vcs:updated")
def handle_port_updated():
    print("[Notification]")
    print("port:updated")


@sio.on("device:updated")
def handle_port_updated():
    print("[Notification]")
    print("device:updated")


class CustomSemaphore(asyncio.Semaphore):
    def __init__(self, value=0, custom_value=None):
        super().__init__(value)
        self.custom_value = custom_value

    def set_custom_value(self, value):
        self.custom_value = value


async def send(event, param=None):
    sema = CustomSemaphore()

    def callback_handler(result):
        sema.set_custom_value(result)
        sema.release()

    print(f"[Request]")
    print(event)
    await sio.emit(event, param, callback=callback_handler)
    await sema.acquire()
    result = sema.custom_value

    print(f"[Response]")
    print_result(result)
    return result


def print_result(data):
    print(dump(data, sort_keys=False, default_flow_style=False))


# Connect event handler
@sio.event
async def connect():
    print("Connected to the server")


# Disconnect event handler
@sio.event
def disconnect():
    print("Disconnected from server")


# Bind & unbind
async def bind(vcs: int, vppb: int, physical_port: int, ld_id: int = 0):
    await sio.connect("http://0.0.0.0:8200")
    await send(
        "vcs:bind",
        {"virtualCxlSwitchId": vcs, "vppbId": vppb, "physicalPortId": physical_port, "ldId": ld_id},
    )
    await sio.disconnect()


async def unbind(vcs: int, vppb: int):
    await sio.connect("http://0.0.0.0:8200")
    await send(
        "vcs:unbind",
        {"virtualCxlSwitchId": vcs, "vppbId": vppb},
    )
    await sio.disconnect()


# Main asynchronous function to start the client
async def start_client():
    await sio.connect("http://0.0.0.0:8200")
    await sio.wait()


# Stop the client gracefully
async def stop_client():
    await sio.disconnect()
    sys.exit()


# Run the client
if __name__ == "__main__":
    asyncio.run(start_client())
