#!/usr/bin/env python3
import argparse
import fcntl
import random
import select
import socket
import struct
import sys
import time

DHCP_CLIENT_PORT = 68
DHCP_SERVER_PORT = 67

BOOTREQUEST = 1
BOOTREPLY = 2
HTYPE_ETHERNET = 1
HLEN_ETHERNET = 6
MAGIC_COOKIE = b"\x63\x82\x53\x63"

OPT_SUBNET_MASK = 1
OPT_ROUTER = 3
OPT_DNS = 6
OPT_HOSTNAME = 12
OPT_DOMAIN_NAME = 15
OPT_MSG_TYPE = 53
OPT_SERVER_ID = 54
OPT_PARAM_REQ = 55
OPT_END = 255

DHCPDISCOVER = 1
DHCPOFFER = 2
DHCPREQUEST = 3
DHCPACK = 5
DHCPINFORM = 8

SIOCGIFADDR = 0x8915
SIOCGIFHWADDR = 0x8927


def get_iface_ipv4(ifname: str) -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        ifreq = struct.pack("256s", ifname.encode("utf-8")[:15])
        res = fcntl.ioctl(s.fileno(), SIOCGIFADDR, ifreq)
        return socket.inet_ntoa(res[20:24])
    finally:
        s.close()


def get_iface_mac(ifname: str) -> bytes:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        ifreq = struct.pack("256s", ifname.encode("utf-8")[:15])
        res = fcntl.ioctl(s.fileno(), SIOCGIFHWADDR, ifreq)
        return res[18:24]
    finally:
        s.close()


def mac_to_str(mac: bytes) -> str:
    return ":".join(f"{b:02x}" for b in mac)


def build_packet(msg_type: int, xid: int, mac: bytes, client_ip: str | None) -> bytes:
    if len(mac) != 6:
        raise ValueError("Ethernet MAC must be 6 bytes")

    ciaddr = socket.inet_aton(client_ip) if client_ip else b"\x00\x00\x00\x00"
    yiaddr = b"\x00\x00\x00\x00"
    siaddr = b"\x00\x00\x00\x00"
    giaddr = b"\x00\x00\x00\x00"

    # Broadcast for DISCOVER, unicast-ish semantics for INFORM
    flags = 0x8000 if msg_type == DHCPDISCOVER else 0

    chaddr = mac + (b"\x00" * 10)
    sname = b"\x00" * 64
    bootfile = b"\x00" * 128

    bootp = struct.pack(
        "!BBBBIHH4s4s4s4s16s64s128s",
        BOOTREQUEST,         # op
        HTYPE_ETHERNET,      # htype
        HLEN_ETHERNET,       # hlen
        0,                   # hops
        xid,                 # xid
        0,                   # secs
        flags,               # flags
        ciaddr,              # ciaddr
        yiaddr,              # yiaddr
        siaddr,              # siaddr
        giaddr,              # giaddr
        chaddr,              # chaddr
        sname,               # sname
        bootfile,            # file
    )

    hostname = socket.gethostname().encode("ascii", "ignore")[:255]
    prl = bytes([
        OPT_SUBNET_MASK,
        OPT_ROUTER,
        OPT_DNS,
        OPT_DOMAIN_NAME,
        OPT_SERVER_ID,
    ])

    opts = bytearray()
    opts += MAGIC_COOKIE
    opts += struct.pack("BBB", OPT_MSG_TYPE, 1, msg_type)
    opts += struct.pack("BB", OPT_PARAM_REQ, len(prl)) + prl
    if hostname:
        opts += struct.pack("BB", OPT_HOSTNAME, len(hostname)) + hostname
    opts += bytes([OPT_END])

    return bootp + opts


def parse_options(pkt: bytes) -> dict[int, list[bytes]]:
    if len(pkt) < 240:
        return {}
    if pkt[236:240] != MAGIC_COOKIE:
        return {}

    opts: dict[int, list[bytes]] = {}
    i = 240
    while i < len(pkt):
        code = pkt[i]
        i += 1

        if code == OPT_END:
            break
        if code == 0:
            continue
        if i >= len(pkt):
            break

        length = pkt[i]
        i += 1
        if i + length > len(pkt):
            break

        value = pkt[i:i + length]
        i += length
        opts.setdefault(code, []).append(value)

    return opts


def decode_ipv4_list(raw: bytes) -> list[str]:
    if len(raw) % 4 != 0:
        return []
    return [socket.inet_ntoa(raw[i:i + 4]) for i in range(0, len(raw), 4)]


def recv_matching_reply(sock: socket.socket, xid: int, timeout: float) -> dict | None:
    deadline = time.time() + timeout

    while True:
        remain = deadline - time.time()
        if remain <= 0:
            return None

        readable, _, _ = select.select([sock], [], [], remain)
        if not readable:
            return None

        pkt, addr = sock.recvfrom(4096)
        if len(pkt) < 240:
            continue

        try:
            hdr = struct.unpack("!BBBBIHH4s4s4s4s16s64s128s", pkt[:236])
        except struct.error:
            continue

        op = hdr[0]
        rx_xid = hdr[4]
        yiaddr = socket.inet_ntoa(hdr[8])
        siaddr = socket.inet_ntoa(hdr[9])

        if op != BOOTREPLY or rx_xid != xid:
            continue

        opts = parse_options(pkt)
        if not opts:
            continue

        msg_type = None
        if OPT_MSG_TYPE in opts and opts[OPT_MSG_TYPE] and len(opts[OPT_MSG_TYPE][0]) == 1:
            msg_type = opts[OPT_MSG_TYPE][0][0]

        dns_servers = []
        for item in opts.get(OPT_DNS, []):
            dns_servers.extend(decode_ipv4_list(item))

        server_ids = []
        for item in opts.get(OPT_SERVER_ID, []):
            server_ids.extend(decode_ipv4_list(item))

        return {
            "udp_src": addr[0],
            "server_ip_header": siaddr,
            "your_ip": yiaddr,
            "msg_type": msg_type,
            "server_ids": server_ids,
            "dns_servers": dns_servers,
        }


def open_socket_on_iface(ifname: str) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except OSError:
            pass
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    if hasattr(socket, "SO_BINDTODEVICE"):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, ifname.encode() + b"\x00")

    sock.bind(("", DHCP_CLIENT_PORT))
    return sock


def send_and_wait(sock: socket.socket, pkt: bytes, xid: int, timeout: float) -> dict | None:
    sock.sendto(pkt, ("255.255.255.255", DHCP_SERVER_PORT))
    return recv_matching_reply(sock, xid, timeout)


def main() -> int:
    ap = argparse.ArgumentParser(description="Query DHCP for DNS servers")
    ap.add_argument("--iface", required=True, help="Interface name, e.g. eth0 or wlp2s0")
    ap.add_argument("--timeout", type=float, default=2.5, help="Seconds to wait per attempt")
    args = ap.parse_args()

    try:
        ip = get_iface_ipv4(args.iface)
        mac = get_iface_mac(args.iface)
    except OSError as e:
        print(f"Failed to query interface {args.iface}: {e}", file=sys.stderr)
        return 2

    print(f"Interface:   {args.iface}")
    print(f"IPv4:        {ip}")
    print(f"MAC:         {mac_to_str(mac)}")

    try:
        sock = open_socket_on_iface(args.iface)
    except PermissionError:
        print("Need root/admin to bind to UDP 68.", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"Failed to open DHCP socket: {e}", file=sys.stderr)
        return 1

    try:
        # Try INFORM first
        xid = random.getrandbits(32)
        inform = build_packet(DHCPINFORM, xid, mac, ip)
        print("Trying DHCPINFORM...")
        reply = send_and_wait(sock, inform, xid, args.timeout)

        if reply is None:
            # Fall back to DISCOVER
            xid = random.getrandbits(32)
            discover = build_packet(DHCPDISCOVER, xid, mac, None)
            print("No INFORM reply. Trying DHCPDISCOVER...")
            reply = send_and_wait(sock, discover, xid, args.timeout)

        if reply is None:
            print("No DHCP reply received.")
            return 1

        print("Reply received:")
        print(f"  UDP source:      {reply['udp_src']}")
        if reply["server_ids"]:
            print(f"  DHCP server ID:  {', '.join(reply['server_ids'])}")
        if reply["dns_servers"]:
            print(f"  DNS servers:     {', '.join(reply['dns_servers'])}")
            print(f"  Preferred DNS:   {reply['dns_servers'][0]}")
        else:
            print("  DNS servers:     <option 6 not present>")
        return 0

    finally:
        sock.close()


if __name__ == "__main__":
    raise SystemExit(main())