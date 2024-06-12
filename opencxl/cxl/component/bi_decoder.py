"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from abc import abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TypedDict, Optional
from opencxl.util.unaligned_bit_structure import (
    BitMaskedBitStructure,
    BitField,
    StructureField,
    ByteField,
    FIELD_ATTR,
    ShareableByteArray,
)

from opencxl.util.logger import logger


class CXL_DEVICE_TYPE(Enum):
    MEM_DEVICE = auto()
    SWITCH = auto()
    HOST_BRIDGE = auto()
    DSP = auto()
    ROOT_PORT = auto()


class CxlBIDecoderCapabilities(TypedDict):
    # pylint: disable=duplicate-code
    hdm_d_compatible: int
    explicit_bi_decoder_commit_required: int


class CxlBIDecoderControlRegisterOptions(TypedDict):
    bi_forward: int
    bi_enable: int
    bi_decoder_commit_required: int
    device_type: CXL_DEVICE_TYPE


class CXLBIDecoderCapabilityRegisterOptions(TypedDict):
    hdm_d_compatible: int
    explicit_bi_decoder_commit_required: int


class CxlBIDecoderCommitTimeoutScale(Enum):
    ONE_NS = 0b0000
    TEN_NS = 0b0001
    HUNDRED_NS = 0b0010
    ONE_MS = 0b0011
    TEN_MS = 0b0100
    HUNDRED_MS = 0b0101
    ONE_S = 0b0110
    TEN_S = 0b0111


class CxlBIDecoderStatusRegisterOptions(TypedDict):
    bi_decoder_committed: int
    bi_decoder_error_not_committed: int
    reserved1: int
    bi_decoder_commit_timeout_scale: CxlBIDecoderCommitTimeoutScale
    bi_decoder_commit_timeout_base: int
    reserved2: int


class CxlBIDecoderCapabilityStructureOptions(TypedDict):
    capability_options: CXLBIDecoderCapabilityRegisterOptions
    control_options: CxlBIDecoderControlRegisterOptions
    status_options: CxlBIDecoderStatusRegisterOptions


class CxlBIDecoderCapabilityRegister(BitMaskedBitStructure):
    hdm_d_compatible: int
    explicit_bi_decoder_commit_required: int

    def __init__(
        self,
        data: Optional[ShareableByteArray] = None,
        parent_name: Optional[str] = None,
        options: Optional[CxlBIDecoderCapabilityStructureOptions] = None,
    ):
        if not options:
            raise Exception("options is required")

        options = options["capability_options"]
        device_type = options["device_type"]
        hdm_d_compatible = options["hdm_d_compatible"]
        self._fields = [
            BitField(
                "hdm_d_compatible",
                0,
                0,
                (
                    FIELD_ATTR.RESERVED
                    if device_type == CXL_DEVICE_TYPE.DSP
                    or device_type == CXL_DEVICE_TYPE.ROOT_PORT
                    else FIELD_ATTR.HW_INIT
                ),
            ),
            BitField(
                "explicit_bi_decoder_commit_required",
                1,
                1,
                (
                    FIELD_ATTR.RESERVED
                    if device_type == CXL_DEVICE_TYPE.MEM_DEVICE
                    or device_type == CXL_DEVICE_TYPE.ROOT_PORT
                    else FIELD_ATTR.HW_INIT
                ),
                default=options["explicit_bi_decoder_commit_required"],
            ),
            BitField("reserved", 2, 31, FIELD_ATTR.RESERVED),
        ]

        super().__init__(data, parent_name)


class CxlBIDecoderControlRegister(BitMaskedBitStructure):
    bi_forward: int
    bi_enable: int
    bi_decoder_commit: int
    rsvd: int
    device_type: CXL_DEVICE_TYPE

    def __init__(
        self,
        data: Optional[ShareableByteArray] = None,
        parent_name: Optional[str] = None,
        options: Optional[CxlBIDecoderCapabilityStructureOptions] = None,
    ):
        if not options:
            raise Exception("options is required")

        capability_options = options["capability_options"]
        options = options["control_options"]
        self.bi_forward = options["bi_forward"]
        self.bi_enable = options["bi_enable"]
        self.bi_decoder_commit = options["bi_decoder_commit"]
        device_type = options["device_type"]
        self.parent_name = parent_name

        bi_forward_attr = (
            FIELD_ATTR.RESERVED if device_type == CXL_DEVICE_TYPE.MEM_DEVICE else FIELD_ATTR.RW
        )

        bi_decoder_commit_attr = (
            FIELD_ATTR.RESERVED
            if capability_options["explicit_bi_decoder_commit_required"] == 0
            else FIELD_ATTR.RW
        )

        self._fields = [
            BitField("bi_forward", 0, 0, bi_forward_attr),
            BitField("bi_enable", 1, 1, FIELD_ATTR.RW),
            BitField("bi_decoder_commit", 2, 2, bi_decoder_commit_attr),
            BitField("reserved", 3, 31, FIELD_ATTR.RESERVED),
        ]

        super().__init__(data, parent_name)


class CxlBIDecoderStatusRegister(BitMaskedBitStructure):
    bi_decoder_committed: int
    bi_decoder_error_not_committed: int
    reserved1: int
    bi_decoder_commit_timeout_scale: CxlBIDecoderCommitTimeoutScale
    bi_decoder_commit_timeout_base: int
    reserved2: int

    def __init__(
        self,
        data: Optional[ShareableByteArray] = None,
        parent_name: Optional[str] = None,
        options: Optional[CxlBIDecoderControlRegisterOptions] = None,
    ):
        if not options:
            raise Exception("options is required")

        self.bi_decoder_committed = options["bi_decoder_committed"]
        self.bi_decoder_error_not_committed = options["bi_decoder_error_not_committed"]
        self.bi_decoder_commit_timeout_scale = options["bi_decoder_commit_timeout_scale"]
        self.bi_decoder_commit_timeout_base = options["bi_decoder_commit_timeout_base"]
        self.parent_name = parent_name

        self._fields = [
            BitField(
                "bi_decoder_committed", 0, 0, FIELD_ATTR.RO, default=self.bi_decoder_committed
            ),
            BitField(
                "bi_decoder_error_not_committed",
                1,
                1,
                FIELD_ATTR.RO,
                default=self.bi_decoder_error_not_committed,
            ),
            BitField("reserved1", 2, 7, FIELD_ATTR.RESERVED),
            BitField(
                "bi_decoder_commit_timeout_scale",
                8,
                11,
                FIELD_ATTR.HW_INIT,
                default=self.bi_decoder_commit_timeout_scale,
            ),
            BitField(
                "bi_decoder_commit_timeout_base",
                12,
                15,
                FIELD_ATTR.HW_INIT,
                default=self.bi_decoder_commit_timeout_base,
            ),
            BitField("reserved2", 16, 31, FIELD_ATTR.RESERVED),
        ]

        super().__init__(data, parent_name)


class CxlBIDecoderCapabilityStructure(BitMaskedBitStructure):

    def __init__(
        self,
        data: Optional[ShareableByteArray] = None,
        parent_name: Optional[str] = None,
        options: Optional[CxlBIDecoderCapabilityStructureOptions] = None,
    ):
        if not options:
            raise Exception("options is required")
        self._init_global(options)

        super().__init__(data, parent_name)

    def _init_global(self, options: CxlBIDecoderCapabilityStructureOptions):

        self._fields = [
            StructureField(
                "capability",
                0,
                3,
                CxlBIDecoderCapabilityRegister,
                options=options,
            ),
            StructureField(
                "control",
                4,
                7,
                CxlBIDecoderControlRegister,
                options=options,
            ),
            StructureField(
                "status",
                8,
                11,
                CxlBIDecoderStatusRegister,
                options=options,
            ),
            ByteField("reserved1", 12, 15, attribute=FIELD_ATTR.RESERVED),
        ]
