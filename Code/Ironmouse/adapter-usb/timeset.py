#!/usr/bin/env python3
import argparse
import datetime
import os
import socket
import struct
import subprocess
import sys
import ssl
import time
import urllib.request

# You probably don't need to change this unless you're running a time server
# If so, your HTTP server needs to return the X-Date header

GRAPHENE_URL = "https://time.grapheneos.org/generate_204"

HTTPS_SITES = [
    "https://hackaday.com",
    "https://maia.crimew.gay",
    "https://www.jeffgeerling.com/",
    "https://www.weather.gov",
]

NTP_SERVERS = [
    "time.cloudflare.com",
    "time.google.com",
    "pool.ntp.org",
]

# ---------------- helpers ----------------

def require_root(test):
    try:
        if not test and os.geteuid() != 0:
            print("[!] Must be run as root unless using --test")
            sys.exit(1)
    except:
        print("[!] Issue while checking root, this may not work!")

def system_utc():
    return datetime.datetime.now(datetime.timezone.utc)

def set_system_time(dt):
    subprocess.run(["date", "-u", "-s", dt.strftime("%Y-%m-%d %H:%M:%S")], check=True)
    subprocess.run(["hwclock", "--systohc"], check=True)

def verbose_result(label, server_time, corrected, rtt):
    sys_now = system_utc()
    delta = (corrected - sys_now).total_seconds()

    print(f"\n=== {label} ===")
    print(f"Server Time:      {server_time}")
    print(f"RTT:              {rtt*1000:.2f} ms")
    print(f"Corrected Time:   {corrected}")
    print(f"System Time:      {sys_now}")
    print(f"Offset (s):       {delta:+.3f}")

    return corrected, abs(delta)

# ---------------- HTTPS timing ----------------

UA = (
    "Mozilla/5.0 (X11; Linux armv7l) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0 Safari/537.36"
)

def timed_https(url, header, retries=3):

    headers = {
        "User-Agent": UA,
        "Accept": "*/*",
        "Connection": "close"
    }

    for attempt in range(retries):

        try:

            # Resolve DNS first (outside of RTT measurement)
            hostname = urllib.request.urlparse(url).hostname
            socket.getaddrinfo(hostname, 443)

            req = urllib.request.Request(url, method="GET", headers=headers)

            opener = urllib.request.build_opener(
                urllib.request.HTTPRedirectHandler()
            )

            t0 = time.monotonic()

            with opener.open(req, timeout=5) as r:
                t1 = time.monotonic()

                hdr = r.headers.get(header)
                if not hdr:
                    # this server isn't following the HTTP spec, can't be used
                    raise RuntimeError("Missing Date/X-Time header")

            rtt = t1 - t0

            if header == "X-Time":
                # GrapheneOS time server header is milliseconds since epoch
                server_time = datetime.datetime.fromtimestamp(
                    int(hdr)/1000,
                    tz=datetime.timezone.utc
                )
            else:
                # Standard HTTP Date header
                server_time = datetime.datetime.strptime(
                    hdr,
                    "%a, %d %b %Y %H:%M:%S %Z"
                ).replace(tzinfo=datetime.timezone.utc)

            # we assume symmetric latency, so the corrected time is the server time plus half the RTT
            corrected = server_time + datetime.timedelta(seconds=rtt/2)
            return server_time, corrected, rtt

        except Exception as e:
            if attempt == retries - 1:
                raise
            print(f"retry {attempt+1}/{retries} failed → {e}")
            time.sleep(0.4)

# ---------------- NTP timing ----------------

def timed_ntp(server):
    addr = socket.gethostbyname(server)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(5)

    msg = b"\x1b" + 47*b"\0"

    t0 = time.monotonic()
    s.sendto(msg, (addr,123))
    data,_ = s.recvfrom(1024)
    t1 = time.monotonic()

    if len(data)<48:
        raise RuntimeError("Bad NTP")

    t = struct.unpack("!12I", data)[10]-2208988800

    server_time = datetime.datetime.fromtimestamp(
        t, tz=datetime.timezone.utc)

    corrected = server_time + datetime.timedelta(seconds=(t1-t0)/2)

    return server_time, corrected, (t1-t0)

# ---------------- main ----------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fallback", choices=["https","ntp","fail"], default="https")
    parser.add_argument("--test", action="store_true")

    args = parser.parse_args()
    require_root(args.test)

    results=[]

    print("\n>>> GrapheneOS HTTPS Time")
    try:
        st,ct,rtt = timed_https(GRAPHENE_URL,"X-Time")
        results.append(("GrapheneOS",)+verbose_result("GrapheneOS",st,ct,rtt))
    except Exception as e:
        print("GrapheneOS failed:",e)

    print("\n>>> HTTPS Date fallback")
    for s in HTTPS_SITES:
        try:
            st,ct,rtt = timed_https(s,"Date")
            results.append((s,)+verbose_result(s,st,ct,rtt))
        except Exception as e:
            print(s,"failed:",e)

    print("\n>>> NTP fallback")
    for s in NTP_SERVERS:
        try:
            st,ct,rtt = timed_ntp(s)
            results.append((s,)+verbose_result(s,st,ct,rtt))
        except Exception as e:
            print(s,"failed:",e)

    if not results:
        print("No sources usable")
        sys.exit(2)

    best = min(results,key=lambda x:x[2])

    print("\n===== BEST SOURCE =====")
    print("Source:",best[0])
    print("Corrected:",best[1])

    if not args.test:
        print("\nApplying correction...")
        set_system_time(best[1])
    else:
        print("\n--test specified, time NOT changed")

if __name__=="__main__":
    main()