#!/usr/bin/env python3
"""Compilador Determinístico WebAssembly (WASM) para o B3 Integration Atlas C-ABI.

Gera o módulo binário padrão W3C WebAssembly 1.0 (`ffi/b3_atlas_c_abi.wasm`)
100% Python Standard Library (sem necessidade de Emscripten/LLVM externo),
exportando memória linear compartilhada e funções de alta frequência:
- `memory`: Memória Linear WASM (1 página = 64 KB)
- `b3_multilateral_compute_net(receivables_cents: i64, payables_cents: i64) -> i64`
- `b3_custody_position_is_consistent(pos_ptr: i32) -> i32`
- `b3_sbe_validate_new_order(order_ptr: i32, len: i32) -> i32`
"""

from __future__ import annotations

from pathlib import Path
from typing import List


def uleb128(value: int) -> bytes:
    """Codifica inteiro sem sinal em formato LEB128."""
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value != 0:
            byte |= 0x80
        out.append(byte)
        if value == 0:
            break
    return bytes(out)


def sleb128(value: int) -> bytes:
    """Codifica inteiro com sinal em formato LEB128."""
    out = bytearray()
    more = True
    while more:
        byte = value & 0x7F
        value >>= 7
        if (value == 0 and (byte & 0x40) == 0) or (value == -1 and (byte & 0x40) != 0):
            more = False
        else:
            byte |= 0x80
        out.append(byte)
    return bytes(out)


def encode_vec(items: List[bytes]) -> bytes:
    return uleb128(len(items)) + b"".join(items)


def encode_section(section_id: int, payload: bytes) -> bytes:
    return bytes([section_id]) + uleb128(len(payload)) + payload


def encode_string(s: str) -> bytes:
    raw = s.encode("utf-8")
    return uleb128(len(raw)) + raw


# Tipos WASM
I32 = 0x7F
I64 = 0x7E
FUNC_TYPE = 0x60


def build_wasm_bytes() -> bytes:
    """Constrói o binário WebAssembly (`b3_atlas_c_abi.wasm`)."""
    # 1. Type Section (ID 1)
    # Type 0: (i64, i64) -> (i64)  [b3_multilateral_compute_net]
    t0 = bytes([FUNC_TYPE, 2, I64, I64, 1, I64])
    # Type 1: (i32) -> (i32)       [b3_custody_position_is_consistent]
    t1 = bytes([FUNC_TYPE, 1, I32, 1, I32])
    # Type 2: (i32, i32) -> (i32)  [b3_sbe_validate_new_order]
    t2 = bytes([FUNC_TYPE, 2, I32, I32, 1, I32])
    type_sec = encode_section(1, encode_vec([t0, t1, t2]))

    # 2. Function Section (ID 3)
    func_sec = encode_section(3, encode_vec([uleb128(0), uleb128(1), uleb128(2)]))

    # 3. Memory Section (ID 5) — 1 página mínima (64 KB)
    mem_sec = encode_section(5, encode_vec([bytes([0x00, 0x01])]))

    # 4. Export Section (ID 7)
    exports = [
        encode_string("memory") + bytes([0x02, 0x00]),
        encode_string("b3_multilateral_compute_net") + bytes([0x00, 0x00]),
        encode_string("b3_custody_position_is_consistent") + bytes([0x00, 0x01]),
        encode_string("b3_sbe_validate_new_order") + bytes([0x00, 0x02]),
    ]
    export_sec = encode_section(7, encode_vec(exports))

    # 5. Code Section (ID 10)
    # Func 0: b3_multilateral_compute_net(receivables: i64, payables: i64) -> i64
    # local.get 0, local.get 1, i64.sub, end
    f0_body = bytes([
        0x00,              # 0 locals
        0x20, 0x00,        # local.get 0
        0x20, 0x01,        # local.get 1
        0x7D,              # i64.sub
        0x0B,              # end
    ])

    # Func 1: b3_custody_position_is_consistent(pos_ptr: i32) -> i32
    # Offsets em b3_custody_position_t:
    # available_qty (24), blocked_margin_qty (32), blocked_loan_qty (40), blocked_judicial_qty (48), total_qty (56)
    f1_body = bytes([
        0x00,                    # 0 locals
        # Carrega available_qty (offset 24)
        0x20, 0x00,              # local.get 0
        0x29, 0x03, 24,          # i64.load align=8 offset=24
        # Carrega blocked_margin_qty (offset 32)
        0x20, 0x00,              # local.get 0
        0x29, 0x03, 32,          # i64.load align=8 offset=32
        0x7C,                    # i64.add
        # Carrega blocked_loan_qty (offset 40)
        0x20, 0x00,              # local.get 0
        0x29, 0x03, 40,          # i64.load align=8 offset=40
        0x7C,                    # i64.add
        # Carrega blocked_judicial_qty (offset 48)
        0x20, 0x00,              # local.get 0
        0x29, 0x03, 48,          # i64.load align=8 offset=48
        0x7C,                    # i64.add
        # Carrega total_qty (offset 56) e compara
        0x20, 0x00,              # local.get 0
        0x29, 0x03, 56,          # i64.load align=8 offset=56
        0x51,                    # i64.eq -> retorna 1 (true) ou 0 (false)
        0x0B,                    # end
    ])

    # Func 2: b3_sbe_validate_new_order(order_ptr: i32, len: i32) -> i32
    # Verifica len >= 42 e template_id (offset 2, uint16) == 101 e block_length (offset 0, uint16) == 34
    f2_body = (
        bytes([
            0x00,              # 0 locals
            0x20, 0x01,        # local.get 1 (len)
            0x41,              # i32.const 42
        ])
        + sleb128(42)
        + bytes([
            0x4E,              # i32.ge_s
            0x20, 0x00,        # local.get 0 (order_ptr)
            0x2D, 0x00, 0,     # i32.load16_u align=1 offset=0
            0x41,              # i32.const 34
        ])
        + sleb128(34)
        + bytes([
            0x46,              # i32.eq
            0x71,              # i32.and
            0x20, 0x00,        # local.get 0 (order_ptr)
            0x2D, 0x00, 2,     # i32.load16_u align=1 offset=2
            0x41,              # i32.const 101
        ])
        + sleb128(101)
        + bytes([
            0x46,              # i32.eq
            0x71,              # i32.and
            0x0B,              # end
        ])
    )

    code_items = [
        uleb128(len(f0_body)) + f0_body,
        uleb128(len(f1_body)) + f1_body,
        uleb128(len(f2_body)) + f2_body,
    ]
    code_sec = encode_section(10, encode_vec(code_items))

    header = b"\x00asm\x01\x00\x00\x00"
    return header + type_sec + func_sec + mem_sec + export_sec + code_sec


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    wasm_path = repo_root / "ffi" / "b3_atlas_c_abi.wasm"
    wasm_bytes = build_wasm_bytes()
    wasm_path.parent.mkdir(parents=True, exist_ok=True)
    with open(wasm_path, "wb") as f:
        f.write(wasm_bytes)
    print(f"✅ Módulo WebAssembly compilado com sucesso: {wasm_path} ({len(wasm_bytes)} bytes)")


if __name__ == "__main__":
    main()
