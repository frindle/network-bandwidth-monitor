import socket
import struct

# Cloudflare public IPv4 ranges
_RANGES = [
    (0x68100000, 13),   # 104.16.0.0/13
    (0x68180000, 14),   # 104.24.0.0/14
    (0xAC400000, 13),   # 172.64.0.0/13
    (0x83004800, 22),   # 131.0.72.0/22
    (0xA29E0000, 15),   # 162.158.0.0/15
    (0xC629C000, 20),   # 198.41.192.0/20
    (0xC629C800, 21),   # 198.41.200.0/21
]


def is_cloudflare(ip: str) -> bool:
    try:
        ip_int = struct.unpack('>I', socket.inet_aton(ip))[0]
    except OSError:
        return False
    for net, prefix in _RANGES:
        mask = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF
        if (ip_int & mask) == (net & mask):
            return True
    return False
