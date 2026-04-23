"""
edid_parser.py

A practical EDID / E-EDID parser for Python.

Features:
- Parse EDID base block (128 bytes)
- Validate header and checksum
- Decode manufacturer ID, product code, serial, manufacture date
- Parse display parameters, chromaticity, established timings, standard timings
- Parse detailed timing descriptors (DTDs)
- Parse monitor descriptors (name, serial text, range limits, etc.)
- Parse CTA-861 extension blocks
- Parse CTA data blocks: video/audio/vendor-specific/speaker allocation
- Accept bytes, hex strings, or iterables of ints

This module is intentionally pragmatic:
- It parses the most useful/common fields thoroughly.
- It does not attempt to implement every VESA/CTA extension in existence.
- CTA VIC names are provided for common codes and can be extended easily.

Public entry points:
- parse_edid(data) -> EDID
- EDID.from_hex(...)
- EDID.from_file(...)

Python: 3.9+
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union
import json
import math
import re


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class EDIDParseError(ValueError):
    """Raised when EDID data is malformed or cannot be parsed."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EDID_HEADER = b"\x00\xff\xff\xff\xff\xff\xff\x00"


def _u16le(data: bytes, offset: int) -> int:
    return data[offset] | (data[offset + 1] << 8)


def _u32le(data: bytes, offset: int) -> int:
    return (
        data[offset]
        | (data[offset + 1] << 8)
        | (data[offset + 2] << 16)
        | (data[offset + 3] << 24)
    )


def _checksum_ok(block: bytes) -> bool:
    return len(block) == 128 and (sum(block) & 0xFF) == 0


def _decode_manufacturer_id(word: int) -> str:
    # Big-endian 16-bit word split into 5-bit chars:
    # bit 15 reserved, bits 14-10 first letter, 9-5 second, 4-0 third
    c1 = ((word >> 10) & 0x1F) + 64
    c2 = ((word >> 5) & 0x1F) + 64
    c3 = (word & 0x1F) + 64
    chars = []
    for c in (c1, c2, c3):
        if 65 <= c <= 90:
            chars.append(chr(c))
        else:
            chars.append("?")
    return "".join(chars)


def _fraction10(msb: int, lsb2: int) -> float:
    # 10-bit fraction in [0, 1)
    return ((msb << 2) | lsb2) / 1024.0


def _clean_descriptor_text(raw: bytes) -> str:
    # EDID monitor text descriptors are often CP437-ish / ASCII-like and LF terminated.
    text = raw.split(b"\x0a", 1)[0].rstrip(b" ")
    return text.decode("cp437", errors="replace").strip()


def _bits(value: int, hi: int, lo: int) -> int:
    mask = (1 << (hi - lo + 1)) - 1
    return (value >> lo) & mask


def _to_bytes(data: Union[bytes, bytearray, str, Iterable[int]]) -> bytes:
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)

    if isinstance(data, str):
        # Accept:
        # - "00ffffffffffff00..."
        # - "00 ff ff ff ..."
        # - blob with 0x prefixes, commas, whitespace, newlines
        hex_text = data.strip()
        hex_text = re.sub(r"0x", "", hex_text, flags=re.IGNORECASE)
        hex_text = re.sub(r"[^0-9A-Fa-f]", "", hex_text)
        if len(hex_text) % 2 != 0:
            raise EDIDParseError("Hex string has an odd number of hex digits.")
        return bytes.fromhex(hex_text)

    try:
        return bytes(int(x) & 0xFF for x in data)
    except Exception as exc:
        raise EDIDParseError(f"Unsupported EDID input type: {type(data)!r}") from exc


# ---------------------------------------------------------------------------
# Common CTA VIC names (extend as needed)
# ---------------------------------------------------------------------------

CTA_VIC_NAMES: Dict[int, str] = {
    1: "640x480p60",
    2: "720x480p60 4:3",
    3: "720x480p60 16:9",
    4: "1280x720p60",
    5: "1920x1080i60",
    16: "1920x1080p60",
    17: "720x576p50 4:3",
    18: "720x576p50 16:9",
    19: "1280x720p50",
    20: "1920x1080i50",
    31: "1920x1080p50",
    32: "1920x1080p24",
    33: "1920x1080p25",
    34: "1920x1080p30",
    60: "1280x720p24",
    61: "1280x720p25",
    62: "1280x720p30",
    63: "1920x1080p120",
    64: "1920x1080p100",
    93: "3840x2160p24",
    94: "3840x2160p25",
    95: "3840x2160p30",
    96: "3840x2160p50",
    97: "3840x2160p60",
    98: "4096x2160p24",
    99: "4096x2160p25",
    100: "4096x2160p30",
    101: "4096x2160p50",
    102: "4096x2160p60",
    117: "3840x2160p100",
    118: "3840x2160p120",
    193: "5120x2160p120",
    194: "7680x4320p24",
    195: "7680x4320p25",
    196: "7680x4320p30",
    197: "7680x4320p48",
    198: "7680x4320p50",
    199: "7680x4320p60",
    200: "7680x4320p100",
    201: "7680x4320p120",
}


ESTABLISHED_TIMINGS = [
    (35, 7, "720x400@70"),
    (35, 6, "720x400@88"),
    (35, 5, "640x480@60"),
    (35, 4, "640x480@67"),
    (35, 3, "640x480@72"),
    (35, 2, "640x480@75"),
    (35, 1, "800x600@56"),
    (35, 0, "800x600@60"),
    (36, 7, "800x600@72"),
    (36, 6, "800x600@75"),
    (36, 5, "832x624@75"),
    (36, 4, "1024x768i@87"),
    (36, 3, "1024x768@60"),
    (36, 2, "1024x768@70"),
    (36, 1, "1024x768@75"),
    (36, 0, "1280x1024@75"),
    (37, 7, "1152x870@75"),
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class VideoInputAnalog:
    voltage_level: str
    blank_to_black_setup: bool
    separate_sync: bool
    composite_sync_on_hsync: bool
    sync_on_green: bool
    serrated_vsync: bool


@dataclass
class VideoInputDigital:
    bit_depth: Optional[int]
    interface: str


@dataclass
class Chromaticity:
    red_x: float
    red_y: float
    green_x: float
    green_y: float
    blue_x: float
    blue_y: float
    white_x: float
    white_y: float


@dataclass
class StandardTiming:
    horizontal_active: int
    aspect_ratio: str
    vertical_refresh_hz: int
    vertical_active_estimate: Optional[int]


@dataclass
class DetailedTimingDescriptor:
    pixel_clock_mhz: float
    horizontal_active: int
    horizontal_blanking: int
    vertical_active: int
    vertical_blanking: int
    hsync_offset: int
    hsync_pulse_width: int
    vsync_offset: int
    vsync_pulse_width: int
    horizontal_image_mm: int
    vertical_image_mm: int
    horizontal_border: int
    vertical_border: int
    interlaced: bool
    stereo: str
    sync_type: str
    hsync_polarity: Optional[str]
    vsync_polarity: Optional[str]
    raw: bytes = field(repr=False)

    @property
    def horizontal_total(self) -> int:
        return self.horizontal_active + self.horizontal_blanking

    @property
    def vertical_total(self) -> int:
        return self.vertical_active + self.vertical_blanking

    @property
    def refresh_hz(self) -> Optional[float]:
        if self.pixel_clock_mhz <= 0 or self.horizontal_total <= 0 or self.vertical_total <= 0:
            return None
        hz = (self.pixel_clock_mhz * 1_000_000) / (self.horizontal_total * self.vertical_total)
        # For interlaced modes the descriptor's vertical active is typically per field in some contexts;
        # EDID DTD generally uses the encoded total as-is, so this simple calculation is usually fine.
        return hz


@dataclass
class MonitorRangeLimits:
    min_vertical_hz: int
    max_vertical_hz: int
    min_horizontal_khz: int
    max_horizontal_khz: int
    max_pixel_clock_mhz: int
    timing_support: str
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MonitorDescriptor:
    tag: int
    type_name: str
    value: Any
    raw: bytes = field(repr=False)


@dataclass
class CTADataBlock:
    tag: str
    payload: Dict[str, Any]
    raw: bytes = field(repr=False)


@dataclass
class CTAExtension:
    revision: int
    dtd_offset: int
    underscan: bool
    basic_audio: bool
    ycbcr_444: bool
    ycbcr_422: bool
    native_dtd_count: int
    data_blocks: List[CTADataBlock]
    detailed_descriptors: List[Union[DetailedTimingDescriptor, MonitorDescriptor]]
    checksum_valid: bool
    raw: bytes = field(repr=False)


@dataclass
class EDID:
    raw: bytes = field(repr=False)
    header_valid: bool
    checksum_valid: bool
    manufacturer_id: str
    manufacturer_product_code: int
    serial_number: int
    week_of_manufacture: Optional[int]
    year_of_manufacture: Optional[int]
    model_year: Optional[int]
    version: int
    revision: int
    video_input: Union[VideoInputAnalog, VideoInputDigital]
    horizontal_size_cm: int
    vertical_size_cm: int
    gamma: Optional[float]
    features: Dict[str, Any]
    chromaticity: Chromaticity
    established_timings: List[str]
    standard_timings: List[StandardTiming]
    descriptors: List[Union[DetailedTimingDescriptor, MonitorDescriptor]]
    extension_count: int
    extensions: List[Any]
    warnings: List[str] = field(default_factory=list)

    @classmethod
    def from_hex(cls, text: str) -> "EDID":
        return parse_edid(text)

    @classmethod
    def from_file(cls, path: Union[str, Path]) -> "EDID":
        p = Path(path)
        data = p.read_bytes()
        try:
            return parse_edid(data)
        except EDIDParseError:
            # Fallback for text-hex dumps
            return parse_edid(p.read_text(encoding="utf-8", errors="replace"))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


# ---------------------------------------------------------------------------
# Base block parsing
# ---------------------------------------------------------------------------

def _parse_video_input(byte20: int) -> Union[VideoInputAnalog, VideoInputDigital]:
    if byte20 & 0x80:
        bit_depth_code = (byte20 >> 4) & 0x07
        interface_code = byte20 & 0x0F

        bit_depth_map = {
            0b000: None,
            0b001: 6,
            0b010: 8,
            0b011: 10,
            0b100: 12,
            0b101: 14,
            0b110: 16,
        }
        interface_map = {
            0x0: "undefined",
            0x1: "DVI",
            0x2: "HDMIa",
            0x3: "HDMIb",
            0x4: "MDDI",
            0x5: "DisplayPort",
        }
        return VideoInputDigital(
            bit_depth=bit_depth_map.get(bit_depth_code),
            interface=interface_map.get(interface_code, f"reserved(0x{interface_code:X})"),
        )

    voltage_map = {
        0b00: "+0.7/-0.3 V",
        0b01: "+0.714/-0.286 V",
        0b10: "+1.0/-0.4 V",
        0b11: "+0.7/0 V (EVC)",
    }
    return VideoInputAnalog(
        voltage_level=voltage_map[(byte20 >> 5) & 0x03],
        blank_to_black_setup=bool(byte20 & (1 << 4)),
        separate_sync=bool(byte20 & (1 << 3)),
        composite_sync_on_hsync=bool(byte20 & (1 << 2)),
        sync_on_green=bool(byte20 & (1 << 1)),
        serrated_vsync=bool(byte20 & (1 << 0)),
    )


def _parse_gamma(byte23: int) -> Optional[float]:
    if byte23 == 0xFF:
        return None
    return (byte23 + 100) / 100.0


def _parse_features(byte24: int, digital: bool) -> Dict[str, Any]:
    features: Dict[str, Any] = {
        "dpms_standby": bool(byte24 & (1 << 7)),
        "dpms_suspend": bool(byte24 & (1 << 6)),
        "dpms_active_off": bool(byte24 & (1 << 5)),
        "srgb_default_color_space": bool(byte24 & (1 << 2)),
        "preferred_timing_in_descriptor_1": bool(byte24 & (1 << 1)),
        "continuous_timings": bool(byte24 & (1 << 0)),
    }

    type_code = (byte24 >> 3) & 0x03
    if digital:
        features["display_type"] = {
            0b00: "RGB 4:4:4",
            0b01: "RGB 4:4:4 + YCrCb 4:4:4",
            0b10: "RGB 4:4:4 + YCrCb 4:2:2",
            0b11: "RGB 4:4:4 + YCrCb 4:4:4 + YCrCb 4:2:2",
        }[type_code]
    else:
        features["display_type"] = {
            0b00: "monochrome/grayscale",
            0b01: "RGB color",
            0b10: "non-RGB color",
            0b11: "undefined",
        }[type_code]
    return features


def _parse_chromaticity(block: bytes) -> Chromaticity:
    b25 = block[25]
    b26 = block[26]
    red_x_lsb = (b25 >> 6) & 0x03
    red_y_lsb = (b25 >> 4) & 0x03
    green_x_lsb = (b25 >> 2) & 0x03
    green_y_lsb = b25 & 0x03
    blue_x_lsb = (b26 >> 6) & 0x03
    blue_y_lsb = (b26 >> 4) & 0x03
    white_x_lsb = (b26 >> 2) & 0x03
    white_y_lsb = b26 & 0x03

    return Chromaticity(
        red_x=_fraction10(block[27], red_x_lsb),
        red_y=_fraction10(block[28], red_y_lsb),
        green_x=_fraction10(block[29], green_x_lsb),
        green_y=_fraction10(block[30], green_y_lsb),
        blue_x=_fraction10(block[31], blue_x_lsb),
        blue_y=_fraction10(block[32], blue_y_lsb),
        white_x=_fraction10(block[33], white_x_lsb),
        white_y=_fraction10(block[34], white_y_lsb),
    )


def _parse_established_timings(block: bytes) -> List[str]:
    timings = []
    for byte_index, bit_index, name in ESTABLISHED_TIMINGS:
        if block[byte_index] & (1 << bit_index):
            timings.append(name)
    return timings


def _estimate_vertical_from_aspect(horizontal: int, aspect: str) -> Optional[int]:
    ratios = {
        "16:10": (10, 16),
        "4:3": (3, 4),
        "5:4": (4, 5),
        "16:9": (9, 16),
        "1:1": (1, 1),
    }
    if aspect not in ratios:
        return None
    num, den = ratios[aspect]
    return int(round(horizontal * num / den))


def _parse_standard_timings(block: bytes, version: int, revision: int) -> List[StandardTiming]:
    result = []
    for i in range(8):
        b1 = block[38 + i * 2]
        b2 = block[39 + i * 2]
        if b1 == 0x01 and b2 == 0x01:
            continue
        if b1 == 0x00:
            continue

        hactive = (b1 + 31) * 8
        aspect_code = (b2 >> 6) & 0x03
        refresh = (b2 & 0x3F) + 60

        if version == 1 and revision < 3 and aspect_code == 0b00:
            aspect = "1:1"
        else:
            aspect = {
                0b00: "16:10",
                0b01: "4:3",
                0b10: "5:4",
                0b11: "16:9",
            }[aspect_code]

        result.append(
            StandardTiming(
                horizontal_active=hactive,
                aspect_ratio=aspect,
                vertical_refresh_hz=refresh,
                vertical_active_estimate=_estimate_vertical_from_aspect(hactive, aspect),
            )
        )
    return result


def _decode_stereo(bits65: int, bit0: int) -> str:
    table = {
        (0b00, 0): "none",
        (0b00, 1): "none",
        (0b01, 0): "field sequential, right during stereo sync",
        (0b10, 0): "field sequential, left during stereo sync",
        (0b01, 1): "2-way interleaved, right image on even lines",
        (0b10, 1): "2-way interleaved, left image on even lines",
        (0b11, 0): "4-way interleaved",
        (0b11, 1): "side-by-side interleaved",
    }
    return table[(bits65, bit0)]


def _parse_dtd(d: bytes) -> DetailedTimingDescriptor:
    pixel_clock = _u16le(d, 0) * 0.01  # MHz

    h_active = d[2] | ((d[4] >> 4) << 8)
    h_blank = d[3] | ((d[4] & 0x0F) << 8)
    v_active = d[5] | ((d[7] >> 4) << 8)
    v_blank = d[6] | ((d[7] & 0x0F) << 8)

    hsync_offset = d[8] | (_bits(d[11], 7, 6) << 8)
    hsync_width = d[9] | (_bits(d[11], 5, 4) << 8)
    vsync_offset = _bits(d[10], 7, 4) | (_bits(d[11], 3, 2) << 4)
    vsync_width = _bits(d[10], 3, 0) | (_bits(d[11], 1, 0) << 4)

    h_size = d[12] | ((d[14] >> 4) << 8)
    v_size = d[13] | ((d[14] & 0x0F) << 8)

    flags = d[17]
    interlaced = bool(flags & 0x80)
    stereo = _decode_stereo((flags >> 5) & 0x03, flags & 0x01)

    hsync_pol = None
    vsync_pol = None

    if (flags & 0x10) == 0:
        sync_type = "analog"
    else:
        mode = (flags >> 3) & 0x03
        if mode == 0b10:
            sync_type = "digital composite"
            hsync_pol = "positive" if (flags & 0x02) else "negative"
        elif mode == 0b11:
            sync_type = "digital separate"
            vsync_pol = "positive" if (flags & 0x04) else "negative"
            hsync_pol = "positive" if (flags & 0x02) else "negative"
        else:
            sync_type = "digital"

    return DetailedTimingDescriptor(
        pixel_clock_mhz=pixel_clock,
        horizontal_active=h_active,
        horizontal_blanking=h_blank,
        vertical_active=v_active,
        vertical_blanking=v_blank,
        hsync_offset=hsync_offset,
        hsync_pulse_width=hsync_width,
        vsync_offset=vsync_offset,
        vsync_pulse_width=vsync_width,
        horizontal_image_mm=h_size,
        vertical_image_mm=v_size,
        horizontal_border=d[15],
        vertical_border=d[16],
        interlaced=interlaced,
        stereo=stereo,
        sync_type=sync_type,
        hsync_polarity=hsync_pol,
        vsync_polarity=vsync_pol,
        raw=d,
    )


def _parse_range_limits(raw: bytes) -> MonitorRangeLimits:
    offset_flags = raw[4]

    v_min = raw[5]
    v_max = raw[6]
    h_min = raw[7]
    h_max = raw[8]
    max_pixel_clock = raw[9] * 10  # MHz

    v_offset_mode = offset_flags & 0x03
    h_offset_mode = (offset_flags >> 2) & 0x03

    if v_offset_mode == 0b10:
        v_max += 255
    elif v_offset_mode == 0b11:
        v_min += 255
        v_max += 255

    if h_offset_mode == 0b10:
        h_max += 255
    elif h_offset_mode == 0b11:
        h_min += 255
        h_max += 255

    timing_code = raw[10]
    timing_support = {
        0x00: "default GTF",
        0x01: "no timing info",
        0x02: "secondary GTF",
        0x04: "CVT",
    }.get(timing_code, f"unknown(0x{timing_code:02X})")

    extra: Dict[str, Any] = {}

    if timing_code == 0x02:
        extra = {
            "secondary_gtf_start_frequency_khz": raw[12] * 2,
            "secondary_gtf_c": raw[13] / 2.0,
            "secondary_gtf_m": _u16le(raw, 14),
            "secondary_gtf_k": raw[16],
            "secondary_gtf_j": raw[17] / 2.0,
        }
    elif timing_code == 0x04:
        max_active_pixels = ((raw[12] & 0x03) << 8) | raw[13]
        if max_active_pixels == 0:
            max_active_pixels = None

        aspect_bitmap = raw[14]
        preferred_aspect_code = (raw[15] >> 5) & 0x07
        preferred_aspect = {
            0b000: "4:3",
            0b001: "16:9",
            0b010: "16:10",
            0b011: "5:4",
            0b100: "15:9",
        }.get(preferred_aspect_code, "reserved")

        extra = {
            "cvt_version": f"{(raw[11] >> 4) & 0x0F}.{raw[11] & 0x0F}",
            "additional_clock_precision_mhz": ((raw[12] >> 2) & 0x3F) * 0.25,
            "max_active_pixels_per_line": max_active_pixels,
            "supported_aspects": {
                "4:3": bool(aspect_bitmap & (1 << 7)),
                "16:9": bool(aspect_bitmap & (1 << 6)),
                "16:10": bool(aspect_bitmap & (1 << 5)),
                "5:4": bool(aspect_bitmap & (1 << 4)),
                "15:9": bool(aspect_bitmap & (1 << 3)),
            },
            "preferred_aspect": preferred_aspect,
            "supports_cvt_reduced_blanking": bool(raw[15] & (1 << 4)),
            "supports_cvt_standard_blanking": bool(raw[15] & (1 << 3)),
            "scaling_support": {
                "horizontal_shrink": bool(raw[16] & (1 << 7)),
                "horizontal_stretch": bool(raw[16] & (1 << 6)),
                "vertical_shrink": bool(raw[16] & (1 << 5)),
                "vertical_stretch": bool(raw[16] & (1 << 4)),
            },
            "preferred_vertical_refresh_hz": raw[17],
        }

    return MonitorRangeLimits(
        min_vertical_hz=v_min,
        max_vertical_hz=v_max,
        min_horizontal_khz=h_min,
        max_horizontal_khz=h_max,
        max_pixel_clock_mhz=max_pixel_clock,
        timing_support=timing_support,
        extra=extra,
    )


def _parse_monitor_descriptor(d: bytes) -> MonitorDescriptor:
    tag = d[3]
    payload = d[5:18]

    if tag == 0xFF:
        return MonitorDescriptor(tag=tag, type_name="serial_text", value=_clean_descriptor_text(payload), raw=d)
    if tag == 0xFE:
        return MonitorDescriptor(tag=tag, type_name="ascii_text", value=_clean_descriptor_text(payload), raw=d)
    if tag == 0xFC:
        return MonitorDescriptor(tag=tag, type_name="monitor_name", value=_clean_descriptor_text(payload), raw=d)
    if tag == 0xFD:
        return MonitorDescriptor(tag=tag, type_name="range_limits", value=_parse_range_limits(d), raw=d)
    if tag == 0xFB:
        return MonitorDescriptor(tag=tag, type_name="additional_white_point", value={"raw_payload": payload.hex()}, raw=d)
    if tag == 0xFA:
        return MonitorDescriptor(tag=tag, type_name="additional_standard_timings", value={"raw_payload": payload.hex()}, raw=d)
    if tag == 0xF9:
        return MonitorDescriptor(tag=tag, type_name="display_color_management", value={"raw_payload": payload.hex()}, raw=d)
    if tag == 0xF8:
        return MonitorDescriptor(tag=tag, type_name="cvt_3byte_codes", value={"raw_payload": payload.hex()}, raw=d)
    if tag == 0xF7:
        return MonitorDescriptor(tag=tag, type_name="additional_standard_timing_3", value={"raw_payload": payload.hex()}, raw=d)
    if tag == 0x10:
        return MonitorDescriptor(tag=tag, type_name="dummy", value=None, raw=d)
    if 0x00 <= tag <= 0x0F:
        return MonitorDescriptor(tag=tag, type_name="manufacturer_reserved", value={"raw_payload": payload.hex()}, raw=d)

    return MonitorDescriptor(tag=tag, type_name="unknown_monitor_descriptor", value={"raw_payload": payload.hex()}, raw=d)


def _parse_descriptor(d: bytes) -> Union[DetailedTimingDescriptor, MonitorDescriptor]:
    if len(d) != 18:
        raise EDIDParseError("Descriptor must be exactly 18 bytes.")

    if d[0] == 0x00 and d[1] == 0x00:
        return _parse_monitor_descriptor(d)
    return _parse_dtd(d)


# ---------------------------------------------------------------------------
# CTA parsing
# ---------------------------------------------------------------------------

def _parse_cta_video_block(payload: bytes) -> Dict[str, Any]:
    svds = []
    for b in payload:
        # For VICs 1..64, bit 7 indicates native. Above that it is the 8th bit.
        if (b & 0x7F) in range(1, 65):
            native = bool(b & 0x80)
            vic = b & 0x7F
        else:
            native = False
            vic = b

        svds.append(
            {
                "vic": vic,
                "native": native,
                "name": CTA_VIC_NAMES.get(vic),
            }
        )
    return {"svds": svds}


def _parse_cta_audio_block(payload: bytes) -> Dict[str, Any]:
    sads = []
    if len(payload) % 3 != 0:
        # Parse what we can, but record odd length
        pass

    for i in range(0, len(payload) - (len(payload) % 3), 3):
        b1, b2, b3 = payload[i], payload[i + 1], payload[i + 2]
        fmt = (b1 >> 3) & 0x0F
        channels = (b1 & 0x07) + 1

        format_name = {
            0: "reserved",
            1: "LPCM",
            2: "AC-3",
            3: "MPEG-1",
            4: "MP3",
            5: "MPEG-2",
            6: "AAC LC",
            7: "DTS",
            8: "ATRAC",
            9: "DSD",
            10: "DD+",
            11: "DTS-HD",
            12: "MAT/MLP/TrueHD",
            13: "DST",
            14: "WMA Pro",
            15: "Extension",
        }.get(fmt, f"unknown({fmt})")

        freqs = []
        freq_bits = [
            (6, 192),
            (5, 176.4),
            (4, 96),
            (3, 88.2),
            (2, 48),
            (1, 44.1),
            (0, 32),
        ]
        for bit, freq in freq_bits:
            if b2 & (1 << bit):
                freqs.append(freq)

        entry: Dict[str, Any] = {
            "format_code": fmt,
            "format_name": format_name,
            "channels": channels,
            "sample_rates_khz": freqs,
        }

        if fmt == 1:
            depths = []
            if b3 & 0x01:
                depths.append(16)
            if b3 & 0x02:
                depths.append(20)
            if b3 & 0x04:
                depths.append(24)
            entry["bit_depths"] = depths
        elif 2 <= fmt <= 8:
            entry["max_bitrate_kbps"] = b3 * 8
        elif fmt == 15:
            ext_code = (b3 >> 3) & 0x1F
            entry["extension_code"] = ext_code

        sads.append(entry)

    return {"sads": sads}


def _parse_cta_speaker_block(payload: bytes) -> Dict[str, Any]:
    b1 = payload[0] if len(payload) > 0 else 0
    b2 = payload[1] if len(payload) > 1 else 0
    b3 = payload[2] if len(payload) > 2 else 0

    return {
        "front_left_right": bool(b1 & (1 << 0)),
        "lfe": bool(b1 & (1 << 1)),
        "front_center": bool(b1 & (1 << 2)),
        "back_left_right": bool(b1 & (1 << 3)),
        "back_center": bool(b1 & (1 << 4)),
        "front_left_right_center": bool(b1 & (1 << 5)),
        "left_right_surround": bool(b2 & (1 << 3)),
        "top_front_center": bool(b2 & (1 << 2)),
        "top_center": bool(b2 & (1 << 1)),
        "top_front_left_right": bool(b2 & (1 << 0)),
        "bottom_front_left_right_deprecated": bool(b3 & (1 << 2)),
        "bottom_front_center_deprecated": bool(b3 & (1 << 1)),
        "top_back_left_right_deprecated": bool(b3 & (1 << 0)),
    }


def _parse_cta_vendor_block(payload: bytes) -> Dict[str, Any]:
    if len(payload) < 3:
        return {"error": "Vendor Specific Data Block too short", "raw_payload": payload.hex()}

    oui = payload[0] | (payload[1] << 8) | (payload[2] << 16)
    info: Dict[str, Any] = {
        "ieee_oui": f"0x{oui:06X}",
        "raw_payload": payload.hex(),
    }

    known_vendor = {
        0x000C03: "HDMI Licensing, LLC",
        0xC45DD8: "HDMI Forum",
        0x00D046: "Dolby Laboratories",
        0x90848B: "HDR10+ Technologies, LLC",
    }.get(oui)

    if known_vendor:
        info["vendor_name"] = known_vendor

    # HDMI VSDB
    if oui == 0x000C03 and len(payload) >= 5:
        pa_a = payload[3] >> 4
        pa_b = payload[3] & 0x0F
        pa_c = payload[4] >> 4
        pa_d = payload[4] & 0x0F
        info["cec_physical_address"] = f"{pa_a}.{pa_b}.{pa_c}.{pa_d}"

        if len(payload) >= 6:
            dc = payload[5]
            info["deep_color"] = {
                "supports_ai": bool(dc & (1 << 7)),
                "48_bit": bool(dc & (1 << 6)),
                "36_bit": bool(dc & (1 << 5)),
                "30_bit": bool(dc & (1 << 4)),
                "y444_deep_color": bool(dc & (1 << 3)),
                "dvi_dual_link": bool(dc & (1 << 0)),
            }

        if len(payload) >= 7 and payload[6] != 0:
            info["max_tmds_clock_mhz"] = payload[6] * 5

    return info


def _parse_cta_extended_block(payload: bytes) -> Dict[str, Any]:
    if not payload:
        return {"error": "Extended CTA block missing extended tag"}

    ext_tag = payload[0]
    body = payload[1:]

    ext_name = {
        0: "Video Capability",
        1: "Vendor Specific Video",
        2: "VESA Display Device",
        5: "Colorimetry",
        6: "HDR Static Metadata",
        7: "HDR Dynamic Metadata",
        8: "Native Video Resolution",
        13: "Video Format Preference",
        14: "YCbCr 4:2:0 Video",
        15: "YCbCr 4:2:0 Capability Map",
        17: "Vendor Specific Audio",
        18: "HDMI Audio",
        19: "Room Configuration",
        20: "Speaker Location",
        32: "InfoFrame",
        34: "Type VII Video Timing",
        35: "Type VIII Video Timing",
        42: "Type X Video Timing",
        120: "HDMI Forum EDID Extension Override",
        121: "HDMI Forum Sink Capability",
        122: "HDMI Forum Source-Based Tone Mapping",
    }.get(ext_tag, f"extended_tag_{ext_tag}")

    return {
        "extended_tag": ext_tag,
        "extended_tag_name": ext_name,
        "raw_body": body.hex(),
    }


def _parse_cta_block(header: int, payload: bytes) -> CTADataBlock:
    tag_code = (header >> 5) & 0x07
    length = header & 0x1F

    if len(payload) != length:
        raise EDIDParseError("CTA block payload length mismatch.")

    if tag_code == 1:
        return CTADataBlock(tag="audio", payload=_parse_cta_audio_block(payload), raw=bytes([header]) + payload)
    if tag_code == 2:
        return CTADataBlock(tag="video", payload=_parse_cta_video_block(payload), raw=bytes([header]) + payload)
    if tag_code == 3:
        return CTADataBlock(tag="vendor_specific", payload=_parse_cta_vendor_block(payload), raw=bytes([header]) + payload)
    if tag_code == 4:
        return CTADataBlock(tag="speaker_allocation", payload=_parse_cta_speaker_block(payload), raw=bytes([header]) + payload)
    if tag_code == 5:
        return CTADataBlock(tag="vesa_dtcdb", payload={"raw_payload": payload.hex()}, raw=bytes([header]) + payload)
    if tag_code == 6:
        return CTADataBlock(tag="video_format", payload={"raw_payload": payload.hex()}, raw=bytes([header]) + payload)
    if tag_code == 7:
        return CTADataBlock(tag="extended", payload=_parse_cta_extended_block(payload), raw=bytes([header]) + payload)

    return CTADataBlock(tag=f"unknown_{tag_code}", payload={"raw_payload": payload.hex()}, raw=bytes([header]) + payload)


def _parse_cta_extension(block: bytes) -> CTAExtension:
    revision = block[1]
    dtd_offset = block[2]
    flags = block[3]

    underscan = bool(flags & (1 << 7))
    basic_audio = bool(flags & (1 << 6))
    ycbcr_444 = bool(flags & (1 << 5))
    ycbcr_422 = bool(flags & (1 << 4))
    native_dtd_count = flags & 0x0F

    data_blocks: List[CTADataBlock] = []
    descriptors: List[Union[DetailedTimingDescriptor, MonitorDescriptor]] = []

    if dtd_offset == 0:
        dbc_end = 127
        dtd_start = None
    else:
        dbc_end = dtd_offset
        dtd_start = dtd_offset

    # Data Block Collection is bytes 4 .. dtd_offset-1
    idx = 4
    while idx < dbc_end:
        header = block[idx]
        length = header & 0x1F
        payload = block[idx + 1 : idx + 1 + length]
        if idx + 1 + length > len(block) - 1:
            break
        data_blocks.append(_parse_cta_block(header, payload))
        idx += 1 + length

    # Detailed descriptors
    if dtd_start is not None and dtd_start >= 4:
        idx = dtd_start
        while idx + 18 <= 127:
            desc = block[idx : idx + 18]
            if desc == b"\x00" * 18:
                break
            descriptors.append(_parse_descriptor(desc))
            idx += 18

    return CTAExtension(
        revision=revision,
        dtd_offset=dtd_offset,
        underscan=underscan,
        basic_audio=basic_audio,
        ycbcr_444=ycbcr_444,
        ycbcr_422=ycbcr_422,
        native_dtd_count=native_dtd_count,
        data_blocks=data_blocks,
        detailed_descriptors=descriptors,
        checksum_valid=_checksum_ok(block),
        raw=block,
    )


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

def parse_edid(data: Union[bytes, bytearray, str, Iterable[int]]) -> EDID:
    raw = _to_bytes(data)
    if len(raw) < 128:
        raise EDIDParseError(f"EDID data too short: expected at least 128 bytes, got {len(raw)}")
    if len(raw) % 128 != 0:
        raise EDIDParseError(f"EDID length must be a multiple of 128 bytes, got {len(raw)}")

    base = raw[:128]
    header_valid = base[:8] == EDID_HEADER
    checksum_valid = _checksum_ok(base)

    manufacturer_word = (base[8] << 8) | base[9]
    manufacturer_id = _decode_manufacturer_id(manufacturer_word)
    product_code = _u16le(base, 10)
    serial_number = _u32le(base, 12)

    week_raw = base[16]
    year_raw = base[17]
    week_of_manufacture: Optional[int]
    year_of_manufacture: Optional[int]
    model_year: Optional[int]

    if week_raw == 0xFF:
        week_of_manufacture = None
        year_of_manufacture = None
        model_year = 1990 + year_raw
    else:
        week_of_manufacture = week_raw if week_raw != 0 else None
        year_of_manufacture = 1990 + year_raw
        model_year = None

    version = base[18]
    revision = base[19]

    video_input = _parse_video_input(base[20])
    horizontal_size_cm = base[21]
    vertical_size_cm = base[22]
    gamma = _parse_gamma(base[23])
    features = _parse_features(base[24], isinstance(video_input, VideoInputDigital))
    chromaticity = _parse_chromaticity(base)
    established_timings = _parse_established_timings(base)
    standard_timings = _parse_standard_timings(base, version, revision)

    descriptors: List[Union[DetailedTimingDescriptor, MonitorDescriptor]] = []
    for off in (54, 72, 90, 108):
        descriptors.append(_parse_descriptor(base[off : off + 18]))

    extension_count = base[126]
    available_extensions = (len(raw) // 128) - 1
    warnings: List[str] = []

    if extension_count > available_extensions:
        warnings.append(
            f"Base block reports {extension_count} extension(s), but only {available_extensions} extension block(s) are present."
        )

    extensions: List[Any] = []
    for i in range(min(extension_count, available_extensions)):
        ext = raw[128 + i * 128 : 128 + (i + 1) * 128]
        tag = ext[0]
        if tag == 0x02:
            extensions.append(_parse_cta_extension(ext))
        else:
            extensions.append(
                {
                    "tag": tag,
                    "tag_name": {
                        0x00: "Timing Extension",
                        0x02: "CTA EDID Timing Extension",
                        0x10: "VTB-EXT",
                        0x20: "EDID 2.0 Extension",
                        0x40: "DI-EXT",
                        0x50: "LS-EXT",
                        0x60: "MI-EXT",
                        0x70: "DisplayID Extension",
                        0xF0: "Block Map",
                        0xFF: "Manufacturer / DDDB-related Extension",
                    }.get(tag, f"Unknown(0x{tag:02X})"),
                    "checksum_valid": _checksum_ok(ext),
                    "raw": ext.hex(),
                }
            )

    if not header_valid:
        warnings.append("Base block header is invalid.")
    if not checksum_valid:
        warnings.append("Base block checksum is invalid.")

    return EDID(
        raw=raw,
        header_valid=header_valid,
        checksum_valid=checksum_valid,
        manufacturer_id=manufacturer_id,
        manufacturer_product_code=product_code,
        serial_number=serial_number,
        week_of_manufacture=week_of_manufacture,
        year_of_manufacture=year_of_manufacture,
        model_year=model_year,
        version=version,
        revision=revision,
        video_input=video_input,
        horizontal_size_cm=horizontal_size_cm,
        vertical_size_cm=vertical_size_cm,
        gamma=gamma,
        features=features,
        chromaticity=chromaticity,
        established_timings=established_timings,
        standard_timings=standard_timings,
        descriptors=descriptors,
        extension_count=extension_count,
        extensions=extensions,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main() -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Parse EDID / E-EDID data.")
    parser.add_argument(
        "input",
        help="Path to binary/text EDID file, or '-' to read from stdin as hex text.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print parsed EDID as JSON.",
    )
    args = parser.parse_args()

    try:
        if args.input == "-":
            data = sys.stdin.read()
            edid = parse_edid(data)
        else:
            edid = EDID.from_file(args.input)

        if args.json:
            print(edid.to_json(indent=2))
        else:
            print(f"Manufacturer: {edid.manufacturer_id}")
            print(f"Product code: 0x{edid.manufacturer_product_code:04X}")
            print(f"Serial: {edid.serial_number}")
            if edid.model_year is not None:
                print(f"Model year: {edid.model_year}")
            else:
                print(f"Manufactured: week {edid.week_of_manufacture}, year {edid.year_of_manufacture}")
            print(f"EDID version: {edid.version}.{edid.revision}")
            print(f"Extensions: {edid.extension_count}")
            if edid.warnings:
                print("Warnings:")
                for w in edid.warnings:
                    print(f"  - {w}")
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(_main())