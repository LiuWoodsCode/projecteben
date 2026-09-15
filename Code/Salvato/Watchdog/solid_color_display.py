#!/usr/bin/env python3

from __future__ import annotations

import ctypes
import errno
import fcntl
import glob
import os
import select
import stat
import sys
import time

from array import array
from dataclasses import dataclass, field
from typing import Optional


SYS_PIDFD_OPEN = 434
SYS_PIDFD_GETFD = 438

DRM_CLIENT_CAP_UNIVERSAL_PLANES = 2
DRM_CLIENT_CAP_ATOMIC = 3

DRM_MODE_OBJECT_CRTC = 0xCCCCCCCC
DRM_MODE_OBJECT_PLANE = 0xEEEEEEEE

DRM_MODE_ATOMIC_TEST_ONLY = 0x0100

VT_OPENQRY = 0x5600
VT_ACTIVATE = 0x5606
VT_WAITACTIVE = 0x5607
VT_REFRESH_DELAY = 0.1


# ---------------------------------------------------------------------------
# Libraries
# ---------------------------------------------------------------------------

libc = ctypes.CDLL(None, use_errno=True)

try:
    libdrm = ctypes.CDLL("libdrm.so.2", use_errno=True)
except OSError as exc:
    print(f"fatal: cannot load libdrm.so.2: {exc}", file=sys.stderr)
    raise SystemExit(1)


# ---------------------------------------------------------------------------
# libdrm structures
# ---------------------------------------------------------------------------

class DrmModeModeInfo(ctypes.Structure):
    _fields_ = [
        ("clock", ctypes.c_uint32),

        ("hdisplay", ctypes.c_uint16),
        ("hsync_start", ctypes.c_uint16),
        ("hsync_end", ctypes.c_uint16),
        ("htotal", ctypes.c_uint16),
        ("hskew", ctypes.c_uint16),

        ("vdisplay", ctypes.c_uint16),
        ("vsync_start", ctypes.c_uint16),
        ("vsync_end", ctypes.c_uint16),
        ("vtotal", ctypes.c_uint16),
        ("vscan", ctypes.c_uint16),

        ("vrefresh", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("type", ctypes.c_uint32),

        ("name", ctypes.c_char * 32),
    ]


class DrmModeRes(ctypes.Structure):
    _fields_ = [
        ("count_fbs", ctypes.c_int),
        ("fbs", ctypes.POINTER(ctypes.c_uint32)),

        ("count_crtcs", ctypes.c_int),
        ("crtcs", ctypes.POINTER(ctypes.c_uint32)),

        ("count_connectors", ctypes.c_int),
        ("connectors", ctypes.POINTER(ctypes.c_uint32)),

        ("count_encoders", ctypes.c_int),
        ("encoders", ctypes.POINTER(ctypes.c_uint32)),

        ("min_width", ctypes.c_uint32),
        ("max_width", ctypes.c_uint32),
        ("min_height", ctypes.c_uint32),
        ("max_height", ctypes.c_uint32),
    ]


class DrmModeCrtc(ctypes.Structure):
    _fields_ = [
        ("crtc_id", ctypes.c_uint32),
        ("buffer_id", ctypes.c_uint32),

        ("x", ctypes.c_uint32),
        ("y", ctypes.c_uint32),

        ("width", ctypes.c_uint32),
        ("height", ctypes.c_uint32),

        ("mode_valid", ctypes.c_int),
        ("mode", DrmModeModeInfo),

        ("gamma_size", ctypes.c_int),
    ]


class DrmModePlaneRes(ctypes.Structure):
    _fields_ = [
        ("count_planes", ctypes.c_uint32),
        ("planes", ctypes.POINTER(ctypes.c_uint32)),
    ]


class DrmModePlane(ctypes.Structure):
    _fields_ = [
        ("count_formats", ctypes.c_uint32),
        ("formats", ctypes.POINTER(ctypes.c_uint32)),

        ("plane_id", ctypes.c_uint32),
        ("crtc_id", ctypes.c_uint32),
        ("fb_id", ctypes.c_uint32),

        ("crtc_x", ctypes.c_uint32),
        ("crtc_y", ctypes.c_uint32),

        ("x", ctypes.c_uint32),
        ("y", ctypes.c_uint32),

        ("possible_crtcs", ctypes.c_uint32),
        ("gamma_size", ctypes.c_uint32),
    ]


class DrmModeObjectProperties(ctypes.Structure):
    _fields_ = [
        ("count_props", ctypes.c_uint32),
        ("props", ctypes.POINTER(ctypes.c_uint32)),
        ("prop_values", ctypes.POINTER(ctypes.c_uint64)),
    ]


class DrmModePropertyEnum(ctypes.Structure):
    _fields_ = [
        ("value", ctypes.c_uint64),
        ("name", ctypes.c_char * 32),
    ]


class DrmModePropertyRes(ctypes.Structure):
    _fields_ = [
        ("prop_id", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("name", ctypes.c_char * 32),

        ("count_values", ctypes.c_int),
        ("values", ctypes.POINTER(ctypes.c_uint64)),

        ("count_enums", ctypes.c_int),
        ("enums", ctypes.POINTER(DrmModePropertyEnum)),

        ("count_blobs", ctypes.c_int),
        ("blob_ids", ctypes.POINTER(ctypes.c_uint32)),
    ]


# ---------------------------------------------------------------------------
# libdrm prototypes
# ---------------------------------------------------------------------------

libdrm.drmIsMaster.argtypes = [ctypes.c_int]
libdrm.drmIsMaster.restype = ctypes.c_int

libdrm.drmSetMaster.argtypes = [ctypes.c_int]
libdrm.drmSetMaster.restype = ctypes.c_int

libdrm.drmDropMaster.argtypes = [ctypes.c_int]
libdrm.drmDropMaster.restype = ctypes.c_int


libdrm.drmSetClientCap.argtypes = [
    ctypes.c_int,
    ctypes.c_uint64,
    ctypes.c_uint64,
]
libdrm.drmSetClientCap.restype = ctypes.c_int


libdrm.drmModeGetResources.argtypes = [ctypes.c_int]
libdrm.drmModeGetResources.restype = ctypes.POINTER(DrmModeRes)

libdrm.drmModeFreeResources.argtypes = [
    ctypes.POINTER(DrmModeRes)
]
libdrm.drmModeFreeResources.restype = None


libdrm.drmModeGetCrtc.argtypes = [
    ctypes.c_int,
    ctypes.c_uint32,
]
libdrm.drmModeGetCrtc.restype = ctypes.POINTER(DrmModeCrtc)

libdrm.drmModeFreeCrtc.argtypes = [
    ctypes.POINTER(DrmModeCrtc)
]
libdrm.drmModeFreeCrtc.restype = None


libdrm.drmModeGetPlaneResources.argtypes = [ctypes.c_int]
libdrm.drmModeGetPlaneResources.restype = ctypes.POINTER(DrmModePlaneRes)

libdrm.drmModeFreePlaneResources.argtypes = [
    ctypes.POINTER(DrmModePlaneRes)
]
libdrm.drmModeFreePlaneResources.restype = None


libdrm.drmModeGetPlane.argtypes = [
    ctypes.c_int,
    ctypes.c_uint32,
]
libdrm.drmModeGetPlane.restype = ctypes.POINTER(DrmModePlane)

libdrm.drmModeFreePlane.argtypes = [
    ctypes.POINTER(DrmModePlane)
]
libdrm.drmModeFreePlane.restype = None


libdrm.drmModeObjectGetProperties.argtypes = [
    ctypes.c_int,
    ctypes.c_uint32,
    ctypes.c_uint32,
]
libdrm.drmModeObjectGetProperties.restype = (
    ctypes.POINTER(DrmModeObjectProperties)
)

libdrm.drmModeFreeObjectProperties.argtypes = [
    ctypes.POINTER(DrmModeObjectProperties)
]
libdrm.drmModeFreeObjectProperties.restype = None


libdrm.drmModeGetProperty.argtypes = [
    ctypes.c_int,
    ctypes.c_uint32,
]
libdrm.drmModeGetProperty.restype = ctypes.POINTER(DrmModePropertyRes)

libdrm.drmModeFreeProperty.argtypes = [
    ctypes.POINTER(DrmModePropertyRes)
]
libdrm.drmModeFreeProperty.restype = None


libdrm.drmModeAtomicAlloc.argtypes = []
libdrm.drmModeAtomicAlloc.restype = ctypes.c_void_p

libdrm.drmModeAtomicFree.argtypes = [ctypes.c_void_p]
libdrm.drmModeAtomicFree.restype = None

libdrm.drmModeAtomicAddProperty.argtypes = [
    ctypes.c_void_p,
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.c_uint64,
]
libdrm.drmModeAtomicAddProperty.restype = ctypes.c_int

libdrm.drmModeAtomicCommit.argtypes = [
    ctypes.c_int,
    ctypes.c_void_p,
    ctypes.c_uint32,
    ctypes.c_void_p,
]
libdrm.drmModeAtomicCommit.restype = ctypes.c_int


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

@dataclass
class MasterReference:
    pid: int
    remote_fd: int
    duplicated_fd: int
    pidfd: int

    def alive(self) -> bool:
        p = select.poll()
        p.register(self.pidfd, select.POLLIN)
        return not bool(p.poll(0))

    def close(self) -> None:
        for fd in (self.duplicated_fd, self.pidfd):
            try:
                os.close(fd)
            except OSError:
                pass


@dataclass
class PlaneState:
    plane_id: int
    crtc_id: int
    fb_id: int

    alpha_prop_id: int
    original_alpha: int


@dataclass
class CrtcState:
    crtc_id: int
    width: int
    height: int

    background_prop_id: int
    original_background: int

    planes: list[PlaneState] = field(default_factory=list)


@dataclass
class CardState:
    path: str
    fd: int

    original_master: Optional[MasterReference]
    crtcs: list[CrtcState]

    acquired_master: bool = False
    color_active: bool = False


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def err(prefix: str) -> RuntimeError:
    e = ctypes.get_errno()

    if not e:
        return RuntimeError(prefix)

    return RuntimeError(
        f"{prefix}: [errno {e}] {os.strerror(e)}"
    )


def prop_name(prop: DrmModePropertyRes) -> str:
    return bytes(prop.name).split(
        b"\0", 1
    )[0].decode("ascii", errors="replace")


def get_properties(
    fd: int,
    object_id: int,
    object_type: int,
) -> dict[str, tuple[int, int]]:

    result: dict[str, tuple[int, int]] = {}

    props_ptr = libdrm.drmModeObjectGetProperties(
        fd,
        object_id,
        object_type,
    )

    if not props_ptr:
        raise err(
            f"cannot read properties for object {object_id}"
        )

    try:
        props = props_ptr.contents

        for i in range(props.count_props):
            prop_id = int(props.props[i])
            value = int(props.prop_values[i])

            p = libdrm.drmModeGetProperty(
                fd,
                prop_id,
            )

            if not p:
                continue

            try:
                name = prop_name(p.contents)
                result[name] = (prop_id, value)
            finally:
                libdrm.drmModeFreeProperty(p)

    finally:
        libdrm.drmModeFreeObjectProperties(
            props_ptr
        )

    return result


def rgb_to_background(
    r: int,
    g: int,
    b: int,
) -> int:
    """
    VC4 BACKGROUND_COLOR is represented as:

        AAAA RRRR GGGG BBBB

    using 16-bit channels.

    8-bit -> 16-bit expansion is x * 257.
    """

    a16 = 0xFFFF
    r16 = r * 257
    g16 = g * 257
    b16 = b * 257

    return (
        (a16 << 48)
        | (r16 << 32)
        | (g16 << 16)
        | b16
    )


def active_vt_number() -> int:
    with open(
        "/sys/class/tty/tty0/active",
        "r",
        encoding="ascii",
    ) as active_file:
        active_name = active_file.read().strip()

    if not active_name.startswith("tty"):
        raise RuntimeError(
            f"unexpected active VT name: {active_name!r}"
        )

    number = active_name[3:]

    if not number.isdigit() or int(number) <= 0:
        raise RuntimeError(
            f"unexpected active VT name: {active_name!r}"
        )

    return int(number)


def refresh_vt_session() -> None:
    """Recreate a VT leave/return after restoring the desktop.

    This deliberately operates on the kernel VT subsystem rather than on a
    particular compositor. The deactivate/reactivate cycle makes the active
    graphical session perform the same modeset and repaint that occurs when
    the user fixes the display with a manual VT switch.
    """

    original_vt = active_vt_number()
    console_fd = os.open(
        "/dev/tty0",
        os.O_RDWR | os.O_CLOEXEC | os.O_NOCTTY,
    )
    away_from_original = False

    try:
        free_vt = array("i", [0])
        fcntl.ioctl(
            console_fd,
            VT_OPENQRY,
            free_vt,
            True,
        )
        refresh_vt = int(free_vt[0])

        if refresh_vt <= 0:
            raise RuntimeError(
                "no unused virtual terminal is available"
            )

        print(
            f"Refreshing desktop via VT {original_vt} "
            f"-> VT {refresh_vt} -> VT {original_vt}..."
        )

        fcntl.ioctl(
            console_fd,
            VT_ACTIVATE,
            refresh_vt,
        )
        away_from_original = True
        fcntl.ioctl(
            console_fd,
            VT_WAITACTIVE,
            refresh_vt,
        )

        # Give the graphical session time to finish its deactivate event
        # before asking the kernel to activate it again.
        time.sleep(VT_REFRESH_DELAY)

        fcntl.ioctl(
            console_fd,
            VT_ACTIVATE,
            original_vt,
        )
        fcntl.ioctl(
            console_fd,
            VT_WAITACTIVE,
            original_vt,
        )
        away_from_original = False

    finally:
        if away_from_original:
            try:
                fcntl.ioctl(
                    console_fd,
                    VT_ACTIVATE,
                    original_vt,
                )
                fcntl.ioctl(
                    console_fd,
                    VT_WAITACTIVE,
                    original_vt,
                )
            except OSError as exc:
                print(
                    f"warning: failed to return to VT "
                    f"{original_vt}: {exc}",
                    file=sys.stderr,
                )

        os.close(console_fd)


# ---------------------------------------------------------------------------
# pidfd helpers
# ---------------------------------------------------------------------------

def pidfd_open(pid: int) -> int:

    if hasattr(os, "pidfd_open"):
        return os.pidfd_open(pid, 0)

    result = libc.syscall(
        SYS_PIDFD_OPEN,
        ctypes.c_int(pid),
        ctypes.c_uint(0),
    )

    if result < 0:
        e = ctypes.get_errno()
        raise OSError(e, os.strerror(e))

    return int(result)


def pidfd_getfd(
    pidfd: int,
    target_fd: int,
) -> int:

    result = libc.syscall(
        SYS_PIDFD_GETFD,
        ctypes.c_int(pidfd),
        ctypes.c_int(target_fd),
        ctypes.c_uint(0),
    )

    if result < 0:
        e = ctypes.get_errno()
        raise OSError(e, os.strerror(e))

    return int(result)


# ---------------------------------------------------------------------------
# DRM master discovery
# ---------------------------------------------------------------------------

def same_device(
    proc_fd: str,
    target_rdev: int,
) -> bool:

    try:
        st = os.stat(proc_fd)
    except (
        PermissionError,
        FileNotFoundError,
        ProcessLookupError,
        OSError,
    ):
        return False

    return (
        stat.S_ISCHR(st.st_mode)
        and st.st_rdev == target_rdev
    )


def find_existing_master(
    card_path: str,
    our_fd: int,
) -> Optional[MasterReference]:

    if libdrm.drmIsMaster(our_fd) == 1:
        return None

    target_rdev = os.stat(
        card_path
    ).st_rdev

    our_pid = os.getpid()

    for proc_dir in glob.glob("/proc/[0-9]*"):

        try:
            pid = int(
                proc_dir.rsplit("/", 1)[1]
            )
        except ValueError:
            continue

        if pid == our_pid:
            continue

        try:
            fd_names = os.listdir(
                f"{proc_dir}/fd"
            )
        except (
            PermissionError,
            FileNotFoundError,
            ProcessLookupError,
        ):
            continue

        try:
            pfd = pidfd_open(pid)
        except OSError:
            continue

        keep_pidfd = False

        try:
            for name in fd_names:

                try:
                    remote_fd = int(name)
                except ValueError:
                    continue

                proc_fd = (
                    f"{proc_dir}/fd/{remote_fd}"
                )

                if not same_device(
                    proc_fd,
                    target_rdev,
                ):
                    continue

                try:
                    dup = pidfd_getfd(
                        pfd,
                        remote_fd,
                    )
                except OSError:
                    continue

                if libdrm.drmIsMaster(dup) == 1:

                    keep_pidfd = True

                    return MasterReference(
                        pid=pid,
                        remote_fd=remote_fd,
                        duplicated_fd=dup,
                        pidfd=pfd,
                    )

                os.close(dup)

        finally:
            if not keep_pidfd:
                try:
                    os.close(pfd)
                except OSError:
                    pass

    return None


# ---------------------------------------------------------------------------
# DRM enumeration
# ---------------------------------------------------------------------------

def enable_client_caps(fd: int) -> None:

    rc = libdrm.drmSetClientCap(
        fd,
        DRM_CLIENT_CAP_UNIVERSAL_PLANES,
        1,
    )

    if rc != 0:
        raise err(
            "DRM universal planes capability failed"
        )

    rc = libdrm.drmSetClientCap(
        fd,
        DRM_CLIENT_CAP_ATOMIC,
        1,
    )

    if rc != 0:
        raise err(
            "DRM atomic capability failed"
        )


def inspect_card(
    path: str,
) -> Optional[CardState]:

    fd = os.open(
        path,
        os.O_RDWR | os.O_CLOEXEC,
    )

    original_master = None

    try:
        # ---------------------------------------------------------------
        # FIRST determine whether this DRM node actually supports KMS.
        #
        # On the Pi 5, /dev/dri/card0 may be a DRM device that doesn't
        # expose modesetting. Calling drmSetClientCap() on it can return
        # EOPNOTSUPP before we ever get a chance to skip it.
        # ---------------------------------------------------------------

        ctypes.set_errno(0)

        res_ptr = libdrm.drmModeGetResources(fd)

        if not res_ptr:
            e = ctypes.get_errno()

            if e in (
                errno.EOPNOTSUPP,
                errno.ENOTTY,
                errno.ENODEV,
                errno.EINVAL,
            ):
                print(
                    f"    skipping: not a KMS device "
                    f"({os.strerror(e)})"
                )

                os.close(fd)
                return None

            raise RuntimeError(
                f"drmModeGetResources({path}) failed: "
                f"[errno {e}] {os.strerror(e)}"
            )

        # We now know this card actually exposes KMS resources.
        #
        # Enable universal planes / atomic BEFORE enumerating planes,
        # because otherwise cursor/overlay planes may not all be visible.
        try:
            enable_client_caps(fd)
        except Exception:
            libdrm.drmModeFreeResources(res_ptr)
            raise

        try:
            resources = res_ptr.contents

            active_crtcs: dict[int, CrtcState] = {}

            for i in range(resources.count_crtcs):
                crtc_id = int(resources.crtcs[i])

                c_ptr = libdrm.drmModeGetCrtc(
                    fd,
                    crtc_id,
                )

                if not c_ptr:
                    continue

                try:
                    c = c_ptr.contents

                    if (
                        not c.mode_valid
                        or not c.width
                        or not c.height
                    ):
                        continue

                    props = get_properties(
                        fd,
                        crtc_id,
                        DRM_MODE_OBJECT_CRTC,
                    )

                    bg = props.get(
                        "BACKGROUND_COLOR"
                    )

                    if bg is None:
                        raise RuntimeError(
                            f"CRTC {crtc_id} "
                            "has no BACKGROUND_COLOR property"
                        )

                    bg_prop_id, bg_value = bg

                    active_crtcs[crtc_id] = CrtcState(
                        crtc_id=crtc_id,
                        width=int(c.width),
                        height=int(c.height),

                        background_prop_id=bg_prop_id,
                        original_background=bg_value,
                    )

                finally:
                    libdrm.drmModeFreeCrtc(c_ptr)

        finally:
            libdrm.drmModeFreeResources(res_ptr)

        if not active_crtcs:
            os.close(fd)
            return None

        # ---------------------------------------------------------------
        # Discover every currently active plane.
        # ---------------------------------------------------------------

        plane_res_ptr = (
            libdrm.drmModeGetPlaneResources(fd)
        )

        if not plane_res_ptr:
            raise err(
                "drmModeGetPlaneResources failed"
            )

        try:
            plane_resources = plane_res_ptr.contents

            for i in range(
                plane_resources.count_planes
            ):
                plane_id = int(
                    plane_resources.planes[i]
                )

                p_ptr = libdrm.drmModeGetPlane(
                    fd,
                    plane_id,
                )

                if not p_ptr:
                    continue

                try:
                    p = p_ptr.contents

                    crtc_id = int(p.crtc_id)
                    fb_id = int(p.fb_id)

                    if (
                        crtc_id == 0
                        or fb_id == 0
                        or crtc_id not in active_crtcs
                    ):
                        continue

                    props = get_properties(
                        fd,
                        plane_id,
                        DRM_MODE_OBJECT_PLANE,
                    )

                    alpha = props.get("alpha")

                    if alpha is None:
                        raise RuntimeError(
                            f"active plane {plane_id} "
                            f"on CRTC {crtc_id} "
                            "does not expose global alpha"
                        )

                    alpha_prop_id, alpha_value = alpha

                    active_crtcs[
                        crtc_id
                    ].planes.append(
                        PlaneState(
                            plane_id=plane_id,
                            crtc_id=crtc_id,
                            fb_id=fb_id,

                            alpha_prop_id=alpha_prop_id,
                            original_alpha=alpha_value,
                        )
                    )

                finally:
                    libdrm.drmModeFreePlane(p_ptr)

        finally:
            libdrm.drmModeFreePlaneResources(
                plane_res_ptr
            )

        for crtc in active_crtcs.values():

            plane_list = ", ".join(
                str(p.plane_id)
                for p in crtc.planes
            )

            print(
                f"    CRTC {crtc.crtc_id}: "
                f"{crtc.width}x{crtc.height}, "
                f"planes [{plane_list}]"
            )

        original_master = find_existing_master(
            path,
            fd,
        )

        return CardState(
            path=path,
            fd=fd,
            original_master=original_master,
            crtcs=list(active_crtcs.values()),
        )

    except Exception:

        if original_master is not None:
            original_master.close()

        try:
            os.close(fd)
        except OSError:
            pass

        raise

# ---------------------------------------------------------------------------
# Master takeover
# ---------------------------------------------------------------------------

def take_master(
    card: CardState,
) -> None:

    if libdrm.drmIsMaster(card.fd) == 1:
        card.acquired_master = True
        return

    old = card.original_master

    if old is not None:

        print(
            f"{card.path}: dropping master from "
            f"PID {old.pid}, FD {old.remote_fd}"
        )

        rc = libdrm.drmDropMaster(
            old.duplicated_fd
        )

        if rc != 0:
            raise err(
                f"{card.path}: "
                "failed to drop original master"
            )

    rc = libdrm.drmSetMaster(card.fd)

    if rc != 0:

        # Emergency rollback.
        if old is not None:
            try:
                libdrm.drmSetMaster(
                    old.duplicated_fd
                )
            except Exception:
                pass

        raise err(
            f"{card.path}: "
            "failed to acquire DRM master"
        )

    card.acquired_master = True


def return_master(
    card: CardState,
) -> None:

    if card.acquired_master:

        if libdrm.drmIsMaster(card.fd) == 1:
            rc = libdrm.drmDropMaster(
                card.fd
            )

            if rc != 0:
                print(
                    f"warning: {card.path}: "
                    "failed to drop our master",
                    file=sys.stderr,
                )

        card.acquired_master = False

    old = card.original_master

    if old is None:
        return

    if not old.alive():
        print(
            f"{card.path}: original DRM owner "
            f"PID {old.pid} exited"
        )
        return

    rc = libdrm.drmSetMaster(
        old.duplicated_fd
    )

    if rc != 0:
        print(
            f"warning: {card.path}: "
            f"failed to return DRM master "
            f"to PID {old.pid}",
            file=sys.stderr,
        )
    else:
        print(
            f"{card.path}: DRM master returned "
            f"to PID {old.pid}"
        )


# ---------------------------------------------------------------------------
# Atomic commits
# ---------------------------------------------------------------------------

def atomic_add(
    req,
    object_id: int,
    property_id: int,
    value: int,
) -> None:

    rc = libdrm.drmModeAtomicAddProperty(
        req,
        object_id,
        property_id,
        value,
    )

    if rc < 0:
        raise RuntimeError(
            f"cannot add atomic property "
            f"object={object_id} "
            f"property={property_id}"
        )


def commit_takeover(
    card: CardState,
    background: int,
) -> None:

    req = libdrm.drmModeAtomicAlloc()

    if not req:
        raise RuntimeError(
            "drmModeAtomicAlloc failed"
        )

    try:
        for crtc in card.crtcs:

            # Set the requested CRTC background color.
            atomic_add(
                req,
                crtc.crtc_id,
                crtc.background_prop_id,
                background,
            )

            # Make every currently attached plane
            # completely transparent.
            #
            # FB_ID / CRTC_ID / geometry are untouched.
            for plane in crtc.planes:
                atomic_add(
                    req,
                    plane.plane_id,
                    plane.alpha_prop_id,
                    0,
                )

        # Validate everything before actually touching
        # the display.
        rc = libdrm.drmModeAtomicCommit(
            card.fd,
            req,
            DRM_MODE_ATOMIC_TEST_ONLY,
            None,
        )

        if rc != 0:
            raise err(
                f"{card.path}: "
                "solid-color atomic TEST_ONLY failed"
            )

        rc = libdrm.drmModeAtomicCommit(
            card.fd,
            req,
            0,
            None,
        )

        if rc != 0:
            raise err(
                f"{card.path}: "
                "solid-color atomic commit failed"
            )

        card.color_active = True

    finally:
        libdrm.drmModeAtomicFree(req)


def commit_restore(
    card: CardState,
) -> None:

    if not card.color_active:
        return

    req = libdrm.drmModeAtomicAlloc()

    if not req:
        raise RuntimeError(
            "drmModeAtomicAlloc failed during restore"
        )

    try:
        for crtc in card.crtcs:

            atomic_add(
                req,
                crtc.crtc_id,
                crtc.background_prop_id,
                crtc.original_background,
            )

            for plane in crtc.planes:
                atomic_add(
                    req,
                    plane.plane_id,
                    plane.alpha_prop_id,
                    plane.original_alpha,
                )

        rc = libdrm.drmModeAtomicCommit(
            card.fd,
            req,
            DRM_MODE_ATOMIC_TEST_ONLY,
            None,
        )

        if rc != 0:
            raise err(
                f"{card.path}: "
                "restore TEST_ONLY failed"
            )

        rc = libdrm.drmModeAtomicCommit(
            card.fd,
            req,
            0,
            None,
        )

        if rc != 0:
            raise err(
                f"{card.path}: "
                "restore atomic commit failed"
            )

        card.color_active = False

    finally:
        libdrm.drmModeAtomicFree(req)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

class SolidColorDisplay:
    """Temporarily replace every active display with one solid RGB color.

    The color remains displayed until :meth:`return_to_desktop` is called.
    Instances are one-shot because the desktop's DRM ownership and display
    state are captured when the color is first displayed.
    """

    def __init__(self) -> None:
        self._cards: list[CardState] = []
        self._started = False
        self._closed = False

    def display_color(self, red: int, green: int, blue: int) -> None:
        """Display an RGB color; each component must be from 0 through 255."""

        if self._started:
            raise RuntimeError("this display session has already been started")
        if self._closed:
            raise RuntimeError("this display session has already been closed")

        components = (red, green, blue)
        if any(type(component) is not int for component in components):
            raise TypeError("RGB components must be integers")
        if any(not 0 <= component <= 255 for component in components):
            raise ValueError("RGB components must be between 0 and 255")
        if os.geteuid() != 0:
            raise PermissionError("solid-color display must be run as root")

        self._started = True
        color = rgb_to_background(red, green, blue)
        color_hex = f"#{red:02X}{green:02X}{blue:02X}"

        print("Inspecting DRM devices...")

        try:
            for path in sorted(glob.glob("/dev/dri/card*")):
                print(f"  {path}")

                try:
                    card = inspect_card(path)
                except Exception as exc:
                    raise RuntimeError(
                        f"{path}: inspection failed: {exc}"
                    ) from exc

                if card is None:
                    print("    no active KMS CRTCs")
                    continue

                self._cards.append(card)

            if not self._cards:
                raise RuntimeError("no active KMS displays found")

            print()
            print(f"Target background: 0x{color:016x} ({color_hex})")
            print()
            print("Taking DRM master...")

            for card in self._cards:
                take_master(card)

            print()
            print(f"Forcing all active outputs to {color_hex}...")

            for card in self._cards:
                commit_takeover(card, color)

        except BaseException:
            self.return_to_desktop()
            raise

    def return_to_desktop(self) -> None:
        """Restore the captured display state and relinquish DRM ownership."""

        if self._closed:
            return

        self._closed = True
        needs_vt_refresh = any(
            card.color_active and card.original_master is not None
            for card in self._cards
        )

        if any(card.color_active for card in self._cards):
            print("Restoring original display composition...")

        for card in reversed(self._cards):
            try:
                commit_restore(card)
            except Exception as exc:
                print(
                    f"warning: {card.path}: restore failed: {exc}",
                    file=sys.stderr,
                )

        if any(card.acquired_master for card in self._cards):
            print("Returning DRM master...")

        for card in reversed(self._cards):
            if not card.acquired_master:
                continue

            try:
                return_master(card)
            except Exception as exc:
                print(
                    f"warning: {card.path}: master return failed: {exc}",
                    file=sys.stderr,
                )

        if needs_vt_refresh:
            try:
                refresh_vt_session()
            except Exception as exc:
                print(
                    f"warning: VT refresh failed: {exc}",
                    file=sys.stderr,
                )

        for card in self._cards:
            if card.original_master:
                card.original_master.close()

            try:
                os.close(card.fd)
            except OSError:
                pass

        self._cards.clear()

    def __enter__(self) -> "SolidColorDisplay":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.return_to_desktop()


_active_display: Optional[SolidColorDisplay] = None


def display_color(red: int, green: int, blue: int) -> None:
    """Display an RGB color until :func:`return_to_desktop` is called."""

    global _active_display

    if _active_display is not None:
        raise RuntimeError("a solid color is already being displayed")

    display = SolidColorDisplay()
    display.display_color(red, green, blue)
    _active_display = display


def return_to_desktop() -> None:
    """Restore the desktop after a successful :func:`display_color` call."""

    global _active_display

    display = _active_display
    _active_display = None

    if display is not None:
        display.return_to_desktop()
