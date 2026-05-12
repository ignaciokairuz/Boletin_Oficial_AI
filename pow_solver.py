"""
DeepSeekHashV1 Proof-of-Work Solver

Algorithm: Custom Keccak-f[1600] with rounds 1..23 (skipping round 0)
Rate: 136 bytes, Padding: 0x06 + 0x80, Output: 32 bytes little-endian

Input format: f"{salt}_{expire_at}_{nonce}"
The 'challenge' field is the EXPECTED hash output (target).
The solver finds an integer nonce such that hash(prefix + str(nonce)) == challenge.
"""

import struct
import time
import json
import base64
import sys

# Keccak round constants
RC = [
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
]

MASK64 = 0xFFFFFFFFFFFFFFFF


def _rotl64(v, k):
    return ((v << k) | (v >> (64 - k))) & MASK64


def keccak_f23(s):
    """Keccak-f[1600] rounds 1..23 (skipping round 0)."""
    a0, a1, a2, a3, a4 = s[0], s[1], s[2], s[3], s[4]
    a5, a6, a7, a8, a9 = s[5], s[6], s[7], s[8], s[9]
    a10, a11, a12, a13, a14 = s[10], s[11], s[12], s[13], s[14]
    a15, a16, a17, a18, a19 = s[15], s[16], s[17], s[18], s[19]
    a20, a21, a22, a23, a24 = s[20], s[21], s[22], s[23], s[24]

    for r in range(1, 24):
        # Theta
        c0 = a0 ^ a5 ^ a10 ^ a15 ^ a20
        c1 = a1 ^ a6 ^ a11 ^ a16 ^ a21
        c2 = a2 ^ a7 ^ a12 ^ a17 ^ a22
        c3 = a3 ^ a8 ^ a13 ^ a18 ^ a23
        c4 = a4 ^ a9 ^ a14 ^ a19 ^ a24
        d0 = c4 ^ _rotl64(c1, 1)
        d1 = c0 ^ _rotl64(c2, 1)
        d2 = c1 ^ _rotl64(c3, 1)
        d3 = c2 ^ _rotl64(c4, 1)
        d4 = c3 ^ _rotl64(c0, 1)
        a0 ^= d0; a5 ^= d0; a10 ^= d0; a15 ^= d0; a20 ^= d0
        a1 ^= d1; a6 ^= d1; a11 ^= d1; a16 ^= d1; a21 ^= d1
        a2 ^= d2; a7 ^= d2; a12 ^= d2; a17 ^= d2; a22 ^= d2
        a3 ^= d3; a8 ^= d3; a13 ^= d3; a18 ^= d3; a23 ^= d3
        a4 ^= d4; a9 ^= d4; a14 ^= d4; a19 ^= d4; a24 ^= d4

        # Rho + Pi
        b0 = a0
        b10 = _rotl64(a1, 1);  b20 = _rotl64(a2, 62); b5 = _rotl64(a3, 28)
        b15 = _rotl64(a4, 27); b16 = _rotl64(a5, 36); b1 = _rotl64(a6, 44)
        b11 = _rotl64(a7, 6);  b21 = _rotl64(a8, 55); b6 = _rotl64(a9, 20)
        b7 = _rotl64(a10, 3);  b17 = _rotl64(a11, 10); b2 = _rotl64(a12, 43)
        b12 = _rotl64(a13, 25); b22 = _rotl64(a14, 39); b23 = _rotl64(a15, 41)
        b8 = _rotl64(a16, 45);  b18 = _rotl64(a17, 15); b3 = _rotl64(a18, 21)
        b13 = _rotl64(a19, 8);  b14 = _rotl64(a20, 18); b24 = _rotl64(a21, 2)
        b9 = _rotl64(a22, 61);  b19 = _rotl64(a23, 56); b4 = _rotl64(a24, 14)

        # Chi
        a0 = b0 ^ ((~b1 & MASK64) & b2);   a1 = b1 ^ ((~b2 & MASK64) & b3)
        a2 = b2 ^ ((~b3 & MASK64) & b4);   a3 = b3 ^ ((~b4 & MASK64) & b0)
        a4 = b4 ^ ((~b0 & MASK64) & b1);   a5 = b5 ^ ((~b6 & MASK64) & b7)
        a6 = b6 ^ ((~b7 & MASK64) & b8);   a7 = b7 ^ ((~b8 & MASK64) & b9)
        a8 = b8 ^ ((~b9 & MASK64) & b5);   a9 = b9 ^ ((~b5 & MASK64) & b6)
        a10 = b10 ^ ((~b11 & MASK64) & b12); a11 = b11 ^ ((~b12 & MASK64) & b13)
        a12 = b12 ^ ((~b13 & MASK64) & b14); a13 = b13 ^ ((~b14 & MASK64) & b10)
        a14 = b14 ^ ((~b10 & MASK64) & b11); a15 = b15 ^ ((~b16 & MASK64) & b17)
        a16 = b16 ^ ((~b17 & MASK64) & b18); a17 = b17 ^ ((~b18 & MASK64) & b19)
        a18 = b18 ^ ((~b19 & MASK64) & b15); a19 = b19 ^ ((~b15 & MASK64) & b16)
        a20 = b20 ^ ((~b21 & MASK64) & b22); a21 = b21 ^ ((~b22 & MASK64) & b23)
        a22 = b22 ^ ((~b23 & MASK64) & b24); a23 = b23 ^ ((~b24 & MASK64) & b20)
        a24 = b24 ^ ((~b20 & MASK64) & b21)

        # Iota
        a0 ^= RC[r]

    s[0:5]   = [a0 & MASK64, a1 & MASK64, a2 & MASK64, a3 & MASK64, a4 & MASK64]
    s[5:10]  = [a5 & MASK64, a6 & MASK64, a7 & MASK64, a8 & MASK64, a9 & MASK64]
    s[10:15] = [a10 & MASK64, a11 & MASK64, a12 & MASK64, a13 & MASK64, a14 & MASK64]
    s[15:20] = [a15 & MASK64, a16 & MASK64, a17 & MASK64, a18 & MASK64, a19 & MASK64]
    s[20:25] = [a20 & MASK64, a21 & MASK64, a22 & MASK64, a23 & MASK64, a24 & MASK64]


def deepseek_hash(data: bytes) -> bytes:
    """Compute DeepSeekHashV1 (custom Keccak-256, rounds 1..23)."""
    RATE = 136
    state = [0] * 25

    # Absorb full blocks
    off = 0
    while off + RATE <= len(data):
        for i in range(RATE // 8):
            w = int.from_bytes(data[off + i*8 : off + i*8 + 8], 'little')
            state[i] ^= w
        keccak_f23(state)
        off += RATE

    # Pad final block
    final = bytearray(RATE)
    rem = len(data) - off
    final[:rem] = data[off:off+rem]
    final[rem] = 0x06
    final[RATE - 1] |= 0x80
    for i in range(RATE // 8):
        w = int.from_bytes(final[i*8:i*8+8], 'little')
        state[i] ^= w
    keccak_f23(state)

    # Squeeze 32 bytes
    return b''.join(state[i].to_bytes(8, 'little') for i in range(4))


def solve_pow(challenge_hex: str, salt: str, expire_at: int, difficulty: int) -> int:
    """
    Find nonce such that deepseek_hash(prefix + str(nonce)) == challenge.
    prefix = f"{salt}_{expire_at}_"
    """
    target = bytes.fromhex(challenge_hex)
    t0 = int.from_bytes(target[0:8], 'little')
    t1 = int.from_bytes(target[8:16], 'little')
    t2 = int.from_bytes(target[16:24], 'little')
    t3 = int.from_bytes(target[24:32], 'little')

    prefix = f"{salt}_{expire_at}_".encode()
    RATE = 136

    # Pre-absorb prefix into base state
    base_state = [0] * 25
    off = 0
    while off + RATE <= len(prefix):
        for i in range(RATE // 8):
            w = int.from_bytes(prefix[off+i*8:off+i*8+8], 'little')
            base_state[i] ^= w
        keccak_f23(base_state)
        off += RATE
    tail_len = len(prefix) - off
    tail = bytearray(RATE)
    tail[:tail_len] = prefix[off:]

    start_time = time.time()
    for n in range(difficulty):
        # Build nonce string
        nonce_bytes = str(n).encode()
        num_len = len(nonce_bytes)

        s = base_state.copy()
        total_tail = tail_len + num_len

        if total_tail < RATE:
            buf = bytearray(RATE)
            buf[:tail_len] = tail[:tail_len]
            buf[tail_len:total_tail] = nonce_bytes
            buf[total_tail] = 0x06
            buf[RATE - 1] |= 0x80
            for i in range(RATE // 8):
                s[i] ^= int.from_bytes(buf[i*8:i*8+8], 'little')
            keccak_f23(s)
        else:
            # Two-block case
            buf = bytearray(RATE)
            buf[:tail_len] = tail[:tail_len]
            first_part = RATE - tail_len
            buf[tail_len:RATE] = nonce_bytes[:first_part]
            for i in range(RATE // 8):
                s[i] ^= int.from_bytes(buf[i*8:i*8+8], 'little')
            keccak_f23(s)

            buf2 = bytearray(RATE)
            rem = num_len - first_part
            buf2[:rem] = nonce_bytes[first_part:]
            buf2[rem] = 0x06
            buf2[RATE - 1] |= 0x80
            for i in range(RATE // 8):
                s[i] ^= int.from_bytes(buf2[i*8:i*8+8], 'little')
            keccak_f23(s)

        if s[0] == t0 and s[1] == t1 and s[2] == t2 and s[3] == t3:
            elapsed = time.time() - start_time
            print(f"  [PoW] Solved in {elapsed:.2f}s, answer={n}")
            return n

    raise ValueError(f"No solution found within difficulty={difficulty}")


def encode_pow_response(challenge_data: dict, answer: int) -> str:
    """Encode PoW result as base64 JSON for the x-ds-pow-response header."""
    result = {
        "algorithm": challenge_data["algorithm"],
        "challenge": challenge_data["challenge"],
        "salt": challenge_data["salt"],
        "answer": answer,
        "signature": challenge_data["signature"],
        "target_path": challenge_data["target_path"],
    }
    return base64.b64encode(json.dumps(result, separators=(',', ':')).encode()).decode()


# ---------------------------------------------------------------------------
# Test vectors from Kimi K2.6 Agent (from ds2api test suite)
# ---------------------------------------------------------------------------

def test_hash():
    """Verify the hash function against known test vectors."""
    vectors = [
        (b"", "e594808bc5b7151ac160c6d39a02e0a8e261ed588578403099e3561dc40c26b3"),
        (b"testsalt_1700000000_42", "d4a2ea58c89e40887c933484868380c6f803eaa8dc53a3b9df8e431b921a4f09"),
        (b"testsalt_1700000000_100000", "abea2f35796b65486e9be1b36f7878c66cab021e96faa473fdf4decd31f9ba30"),
        (b"abc123salt_1700000000_12345", "74b3b7452745b70e85eb32ee7f0a9ec0381d42dd5137b695da915e104fc390e1"),
    ]

    print("=== Testing DeepSeekHashV1 ===")
    all_pass = True
    for data, expected in vectors:
        result = deepseek_hash(data).hex()
        ok = result == expected
        status = "PASS" if ok else "FAIL"
        print(f"  {status} | input={data[:40]}... | hash={result[:20]}...")
        if not ok:
            print(f"         expected: {expected[:20]}...")
            all_pass = False

    return all_pass


def test_solver():
    """Verify solver with known challenge."""
    print("\n=== Testing PoW Solver ===")
    # From test vectors: hash("abcd_1700000000_59206") should give the challenge
    challenge = deepseek_hash(b"abcd_1700000000_59206").hex()
    print(f"  Challenge: {challenge}")

    answer = solve_pow(challenge, "abcd", 1700000000, 144000)
    ok = answer == 59206
    print(f"  {'PASS' if ok else 'FAIL'} | Expected 59206, got {answer}")
    return ok


if __name__ == "__main__":
    if sys.platform == 'win32':
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    hash_ok = test_hash()
    solver_ok = test_solver()

    print(f"\n{'All tests passed!' if (hash_ok and solver_ok) else 'SOME TESTS FAILED'}")
