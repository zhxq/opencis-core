"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from enum import Enum, IntEnum
from typing import TypedDict, Optional
from opencxl.cxl.component.cxl_component_type import CXL_COMPONENT_TYPE
from opencxl.util.unaligned_bit_structure import (
    BitMaskedBitStructure,
    BitField,
    StructureField,
    FIELD_ATTR,
    ShareableByteArray,
)

from opencxl.util.logger import logger


class CacheBITimeoutScale(IntEnum):
    _1_uS = 0b0000
    _10_uS = 0b0001
    _100_uS = 0b0010
    _1_mS = 0b0011
    _10_mS = 0b0100
    _100_mS = 0b0101
    _1_S = 0b0110
    _10_S = 0b0111


# BI Route Table


# BI Decoder


class CxlBIDecoderControlRegisterOptions(TypedDict):
    bi_forward: int
    bi_enable: int
    bi_decoder_commit: int
    device_type: CXL_COMPONENT_TYPE


class CXLBIDecoderCapabilityRegisterOptions(TypedDict):
    hdm_d_compatible: int
    explicit_bi_decoder_commit_required: int


class CxlBIDecoderStatusRegisterOptions(TypedDict):
    bi_decoder_committed: int
    bi_decoder_error_not_committed: int
    reserved1: int
    bi_decoder_commit_timeout_scale: CacheBITimeoutScale
    bi_decoder_commit_timeout_base: int
    reserved2: int


class CxlBIDecoderCapabilityStructureOptions(TypedDict):
    capability_options: CXLBIDecoderCapabilityRegisterOptions
    control_options: CxlBIDecoderControlRegisterOptions
    status_options: CxlBIDecoderStatusRegisterOptions
    device_type: CXL_COMPONENT_TYPE


class CxlBIDecoderCapabilityRegister(BitMaskedBitStructure):
    hdm_d_compatible: int
    explicit_bi_decoder_commit_required: int

    def __init__(
        self,
        data: Optional[ShareableByteArray] = None,
        parent_name: Optional[str] = None,
        options: Optional[CxlBIDecoderCapabilityStructureOptions] = None,
    ):

        device_type = options["device_type"]
        options = options["capability_options"]
        hdm_d_compatible = 0
        explicit_bi_decoder_commit_required = 0

        if device_type != CXL_COMPONENT_TYPE.DSP and device_type != CXL_COMPONENT_TYPE.R:
            hdm_d_compatible = options["hdm_d_compatible"]
        if device_type != CXL_COMPONENT_TYPE.D2 and device_type != CXL_COMPONENT_TYPE.R:
            explicit_bi_decoder_commit_required = options["explicit_bi_decoder_commit_required"]
        self._fields = [
            BitField(
                "hdm_d_compatible",
                0,
                0,
                (
                    FIELD_ATTR.RESERVED
                    if device_type == CXL_COMPONENT_TYPE.DSP or device_type == CXL_COMPONENT_TYPE.R
                    else FIELD_ATTR.HW_INIT
                ),
                default=hdm_d_compatible,
            ),
            BitField(
                "explicit_bi_decoder_commit_required",
                1,
                1,
                (
                    FIELD_ATTR.RESERVED
                    if device_type == CXL_COMPONENT_TYPE.D2 or device_type == CXL_COMPONENT_TYPE.R
                    else FIELD_ATTR.HW_INIT
                ),
                default=explicit_bi_decoder_commit_required,
            ),
            BitField("reserved", 2, 31, FIELD_ATTR.RESERVED),
        ]

        super().__init__(data, parent_name)


class CxlBIDecoderControlRegister(BitMaskedBitStructure):
    bi_forward: int
    bi_enable: int
    bi_decoder_commit: int
    rsvd: int
    device_type: CXL_COMPONENT_TYPE

    def __init__(
        self,
        data: Optional[ShareableByteArray] = None,
        parent_name: Optional[str] = None,
        options: Optional[CxlBIDecoderCapabilityStructureOptions] = None,
    ):

        device_type = options["device_type"]
        capability_options = options["capability_options"]
        options = options["control_options"]
        bi_forward = options["bi_forward"]
        bi_enable = options["bi_enable"]
        bi_decoder_commit = options["bi_decoder_commit"]
        parent_name = parent_name

        bi_forward_attr = (
            FIELD_ATTR.RESERVED if device_type == CXL_COMPONENT_TYPE.D2 else FIELD_ATTR.RW
        )

        bi_decoder_commit_attr = (
            FIELD_ATTR.RESERVED
            if capability_options["explicit_bi_decoder_commit_required"] == 0
            else FIELD_ATTR.RW
        )

        self._fields = [
            BitField("bi_forward", 0, 0, bi_forward_attr, default=bi_forward),
            BitField("bi_enable", 1, 1, FIELD_ATTR.RW, default=bi_enable),
            BitField("bi_decoder_commit", 2, 2, bi_decoder_commit_attr, default=bi_decoder_commit),
            BitField("reserved", 3, 31, FIELD_ATTR.RESERVED),
        ]

        super().__init__(data, parent_name)


class CxlBIDecoderStatusRegister(BitMaskedBitStructure):
    bi_decoder_committed: int
    bi_decoder_error_not_committed: int
    reserved1: int
    bi_decoder_commit_timeout_scale: CacheBITimeoutScale
    bi_decoder_commit_timeout_base: int
    reserved2: int

    def __init__(
        self,
        data: Optional[ShareableByteArray] = None,
        parent_name: Optional[str] = None,
        options: Optional[CxlBIDecoderCapabilityStructureOptions] = None,
    ):

        options = options["status_options"]
        bi_decoder_committed = options["bi_decoder_committed"]
        bi_decoder_error_not_committed = options["bi_decoder_error_not_committed"]
        bi_decoder_commit_timeout_scale = options["bi_decoder_commit_timeout_scale"]
        bi_decoder_commit_timeout_base = options["bi_decoder_commit_timeout_base"]
        parent_name = parent_name

        self._fields = [
            BitField("bi_decoder_committed", 0, 0, FIELD_ATTR.RO, default=bi_decoder_committed),
            BitField(
                "bi_decoder_error_not_committed",
                1,
                1,
                FIELD_ATTR.RO,
                default=bi_decoder_error_not_committed,
            ),
            BitField("reserved1", 2, 7, FIELD_ATTR.RESERVED),
            BitField(
                "bi_decoder_commit_timeout_scale",
                8,
                11,
                FIELD_ATTR.HW_INIT,
                default=bi_decoder_commit_timeout_scale,
            ),
            BitField(
                "bi_decoder_commit_timeout_base",
                12,
                15,
                FIELD_ATTR.HW_INIT,
                default=bi_decoder_commit_timeout_base,
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
        ]

    @staticmethod
    def get_size_from_options(
        options: Optional[CxlBIDecoderCapabilityStructureOptions] = None,
    ):
        return 0x0C
