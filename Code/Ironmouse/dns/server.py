import socket
import struct
import threading

GOOGLE_DNS = ("8.8.8.8", 53)
LISTEN_PORT = 53  # use 53 if running as admin/root

# --- Load hosts file ---
def load_hosts():
    hosts = {}
    try:
        with open("/etc/hosts", "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                ip = parts[0]
                for host in parts[1:]:
                    hosts[host.lower()] = ip
    except FileNotFoundError:
        # Windows fallback
        try:
            with open(r"C:\Windows\System32\drivers\etc\hosts", "r") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split()
                    ip = parts[0]
                    for host in parts[1:]:
                        hosts[host.lower()] = ip
        except Exception:
            pass
    return hosts

HOSTS = load_hosts()

# --- Parse domain from DNS query ---
def parse_domain(data):
    domain = []
    i = 12  # DNS header is 12 bytes
    length = data[i]

    while length != 0:
        i += 1
        domain.append(data[i:i+length].decode())
        i += length
        length = data[i]

    return ".".join(domain), i + 1


# --- Build DNS response ---
def build_response(query, ip):
    transaction_id = query[:2]
    flags = b'\x81\x80'  # standard response, no error
    qdcount = b'\x00\x01'
    ancount = b'\x00\x01'
    nscount = b'\x00\x00'
    arcount = b'\x00\x00'

    header = transaction_id + flags + qdcount + ancount + nscount + arcount

    domain, end = parse_domain(query)
    question = query[12:end + 4]

    # Answer section
    answer = b'\xc0\x0c'  # pointer to domain name
    answer += b'\x00\x01'  # type A
    answer += b'\x00\x01'  # class IN
    answer += b'\x00\x00\x00\x3c'  # TTL (60s)
    answer += b'\x00\x04'  # data length
    answer += socket.inet_aton(ip)

    return header + question + answer


# --- Forward query to Google DNS ---
def forward_query(data):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2)
    try:
        sock.sendto(data, GOOGLE_DNS)
        response, _ = sock.recvfrom(512)
        return response
    except Exception:
        return None
    finally:
        sock.close()


# --- Handle incoming request ---
def handle_request(data, addr, server_socket):
    try:
        domain, _ = parse_domain(data)
        domain = domain.lower()

        print(f"Query: {domain}")

        if domain in HOSTS:
            ip = HOSTS[domain]
            print(f" → Found in hosts: {ip}")
            response = build_response(data, ip)
        else:
            print(" → Forwarding to Google DNS")
            response = forward_query(data)
            if response is None:
                print(" → Failed to resolve")
                return

        server_socket.sendto(response, addr)

    except Exception as e:
        print(f"Error: {e}")


# --- Main server loop ---
def start_dns_server():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", LISTEN_PORT))

    print(f"DNS server listening on port {LISTEN_PORT}")

    while True:
        data, addr = sock.recvfrom(512)
        threading.Thread(target=handle_request, args=(data, addr, sock)).start()


if __name__ == "__main__":
    start_dns_server()