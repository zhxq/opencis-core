"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

import os
import multiprocessing
import logging
from importlib import import_module
import time
import pyshark
import click

from opencis.util.logger import logger
from opencis.bin import fabric_manager
from opencis.bin import get_info
from opencis.bin import cxl_switch
from opencis.bin import single_logical_device as sld
from opencis.bin import multi_logical_device as mld
from opencis.bin import cxl_host
from opencis.bin import mem
from opencis.bin import packet_runner


@click.group()
def cli():
    pass


def validate_component(ctx, param, components):
    # pylint: disable=unused-argument
    valid_components = [
        "fm",
        "switch",
        "host",
        "host-group",
        "sld",
        "sld-group",
        "mld",
        "mld-group",
        "t1accel-group",
        "t2accel-group",
    ]
    if "all" in components:
        return ("fm", "switch", "host-group", "sld-group", "mld-group")
    for c in components:
        if not c in valid_components:
            raise click.BadParameter(f"Please select from {list(valid_components)}")
    return components


def validate_log_level(ctx, param, level):
    # pylint: disable=unused-argument
    valid_levels = list(logging.getLevelNamesMapping().keys())
    if level:
        level = level.upper()
        if not level in valid_levels:
            raise click.BadParameter(f"Please select from {valid_levels}")
    return level


@cli.command(name="start")
@click.pass_context
@click.option(
    "-c",
    "--comp",
    multiple=True,
    required=True,
    callback=validate_component,
    help='Components. e.g. "-c fm -c switch ..." ',
)
@click.option("--config-file", help="<Config File> input path.")
@click.option("--log-file", help="<Log File> output path.")
@click.option("--pcap-file", help="<Packet Capture File> output path.")
@click.option("--log-level", callback=validate_log_level, help="Specify log level.")
@click.option("--show-timestamp", is_flag=True, default=False, help="Show timestamp.")
@click.option("--show-loglevel", is_flag=True, default=False, help="Show log level.")
@click.option("--show-linenumber", is_flag=True, default=False, help="Show line number.")
def start(
    ctx,
    comp,
    config_file,
    log_level,
    log_file,
    pcap_file,
    show_timestamp,
    show_loglevel,
    show_linenumber,
):
    """Start components"""

    # config file mandatory
    config_components = ["switch", "sld-group", "mld-group", "host-group"]
    for c in comp:
        if c in config_components and not config_file:
            raise click.BadParameter(f"Must specify <config file> for {config_components}")

    if log_level or show_timestamp or show_loglevel or show_linenumber:
        logger.set_stdout_levels(
            loglevel=log_level if log_level else "INFO",
            show_timestamp=show_timestamp,
            show_loglevel=show_loglevel,
            show_linenumber=show_linenumber,
        )

    if log_file:
        logger.create_log_file(
            f"logs/{log_file}",
            loglevel=log_level if log_level else "INFO",
            show_timestamp=show_timestamp,
            show_loglevel=show_loglevel,
            show_linenumber=show_linenumber,
        )

    processes = []
    if pcap_file:
        pcap_proc = multiprocessing.Process(target=start_capture, args=(ctx, pcap_file))
        processes.append(pcap_proc)
        pcap_proc.start()
        time.sleep(2)

    if "fm" in comp:
        p_fm = multiprocessing.Process(target=start_fabric_manager, args=(ctx,))
        processes.append(p_fm)
        p_fm.start()

    if "switch" in comp:
        p_switch = multiprocessing.Process(target=start_switch, args=(ctx, config_file))
        processes.append(p_switch)
        p_switch.start()

    if "t1accel-group" in comp:
        accel = import_module("opencxl.bin.accelerator")
        p_at1group = multiprocessing.Process(
            target=start_accel_group, args=(ctx, config_file, accel.ACCEL_TYPE.T1)
        )
        processes.append(p_at1group)
        p_at1group.start()

    if "t2accel-group" in comp:
        accel = import_module("opencxl.bin.accelerator")
        p_at2group = multiprocessing.Process(
            target=start_accel_group, args=(ctx, config_file, accel.ACCEL_TYPE.T2)
        )
        processes.append(p_at2group)
        p_at2group.start()

    if "sld" in comp:
        p_sld = multiprocessing.Process(target=start_sld, args=(ctx,))
        processes.append(p_sld)
        p_sld.start()
    if "sld-group" in comp:
        p_sgroup = multiprocessing.Process(target=start_sld_group, args=(ctx, config_file))
        processes.append(p_sgroup)
        p_sgroup.start()

    if "mld" in comp:
        p_mld = multiprocessing.Process(target=start_mld, args=(ctx,))
        processes.append(p_mld)
        p_mld.start()
    if "mld-group" in comp:
        p_mgroup = multiprocessing.Process(target=start_mld_group, args=(ctx, config_file))
        processes.append(p_mgroup)
        p_mgroup.start()

    if "host" in comp or "host-group" in comp:
        hm_mode = True
        if hm_mode:
            p_hm = multiprocessing.Process(target=start_host_manager, args=(ctx,))
            processes.append(p_hm)
            p_hm.start()
        if "host" in comp:
            p_host = multiprocessing.Process(target=start_host, args=(ctx,))
            processes.append(p_host)
            p_host.start()
        elif "host-group" in comp:
            p_hgroup = multiprocessing.Process(target=start_host_group, args=(ctx, config_file))
            processes.append(p_hgroup)
            p_hgroup.start()


# helper functions
def start_capture(ctx, pcap_file):
    def capture(pcap_file):
        logger.info(f"Capturing in pid: {os.getpid()}")
        if os.path.exists(pcap_file):
            os.remove(pcap_file)

        capture = pyshark.LiveCapture(interface="lo", bpf_filter="tcp", output_file=pcap_file)
        capture.sniff(packet_count=0)

    ctx.invoke(capture, pcap_file=pcap_file)


def start_host_manager(ctx):
    ctx.invoke(cxl_host.start_host_manager)


def start_fabric_manager(ctx):
    ctx.invoke(fabric_manager.start)


def start_switch(ctx, config_file):
    ctx.invoke(cxl_switch.start, config_file=config_file)


def start_host(ctx):
    ctx.invoke(cxl_host.start)


def start_host_group(ctx, config_file):
    ctx.invoke(cxl_host.start_group, config_file=config_file)


def start_sld(ctx, config_file):
    ctx.invoke(sld.start, config_file=config_file)


def start_sld_group(ctx, config_file):
    ctx.invoke(sld.start_group, config_file=config_file)


def start_mld(ctx, config_file):
    ctx.invoke(mld.start, config_file=config_file)


def start_mld_group(ctx, config_file):
    ctx.invoke(mld.start_group, config_file=config_file)


def start_accel_group(ctx, config_file, dev_type):
    accel = import_module("opencis.bin.accelerator")
    ctx.invoke(accel.start_group, config_file=config_file, dev_type=dev_type)


cli.add_command(cxl_host.host_group)
cli.add_command(fabric_manager.fabric_manager_group)
cli.add_command(get_info.get_info_group)
cli.add_command(mem.mem_group)
cli.add_command(packet_runner.ptr_group)

if __name__ == "__main__":
    cli()
