"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from dataclasses import dataclass, field
from opencxl.pci.component.fifo_pair import FifoPair
from opencxl.pci.component.pci_connection import PciConnection


@dataclass
class CxlConnection(PciConnection):
    cxl_mem_fifo: FifoPair = field(default_factory=FifoPair)
    cxl_cache_fifo: FifoPair = field(default_factory=FifoPair)
    cci_fifo: FifoPair = field(default_factory=FifoPair)
