/**
 * @file b3_atlas_c_abi.h
 * @brief B3 Integration Atlas - C-ABI Specification & Formal C99 Headers
 *
 * Foreign Function Interface (FFI) interoperability layer for high-frequency
 * trading (HFT), binary protocol encoding/decoding, custody books, and central
 * clearing liquidity transfers. Compatible with C, C++, Rust, Go (cgo), Zig,
 * and Java (Project Panama / JNI).
 *
 * All wire structures adhere strictly to Little-Endian SBE v2 layout.
 */

#ifndef B3_ATLAS_C_ABI_H
#define B3_ATLAS_C_ABI_H

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ========================================================================== */
/* Protocol Constants & Scales                                                */
/* ========================================================================== */

#define B3_PRICE_SCALE               100000000LL  /**< 10^8 scaling for price */
#define B3_CENT_SCALE                100LL        /**< 10^2 scaling for BRL currency cents */
#define B3_CACHE_LINE_SIZE           64           /**< 64-byte L1 CPU cache-line alignment */

#define B3_DEFAULT_SCHEMA_ID         1
#define B3_DEFAULT_SCHEMA_VERSION    1

#define B3_TEMPLATE_NEW_ORDER        101
#define B3_TEMPLATE_ORDER_CANCEL     102
#define B3_TEMPLATE_EXECUTION_REPORT 103

/* Return error codes for FFI functions */
#define B3_OK                        0
#define B3_ERR_NULL_POINTER         -1
#define B3_ERR_BUFFER_TOO_SMALL     -2
#define B3_ERR_INVALID_TEMPLATE     -3
#define B3_ERR_INVALID_BLOCK_LENGTH -4
#define B3_ERR_VALIDATION_FAILED    -5

/* ========================================================================== */
/* Enumerations                                                               */
/* ========================================================================== */

/**
 * @brief Order Side in PUMA Trading System.
 */
typedef enum b3_side {
    B3_SIDE_BUY  = 1,
    B3_SIDE_SELL = 2
} b3_side_t;

/**
 * @brief Time In Force (Validity) of the order.
 */
typedef enum b3_time_in_force {
    B3_TIF_DAY = 0,
    B3_TIF_GTC = 1,
    B3_TIF_IOC = 3,
    B3_TIF_FOK = 4
} b3_time_in_force_t;

/**
 * @brief Execution Event Type.
 */
typedef enum b3_exec_type {
    B3_EXEC_NEW          = 0,
    B3_EXEC_PARTIAL_FILL = 1,
    B3_EXEC_FILL         = 2,
    B3_EXEC_CANCELLED    = 4,
    B3_EXEC_REJECTED     = 8
} b3_exec_type_t;

/**
 * @brief Order Lifecycle Status.
 */
typedef enum b3_ord_status {
    B3_ORD_NEW              = 0,
    B3_ORD_PARTIALLY_FILLED = 1,
    B3_ORD_FILLED           = 2,
    B3_ORD_CANCELLED        = 4,
    B3_ORD_REJECTED         = 8
} b3_ord_status_t;

/**
 * @brief Settlement Cycle in Central Clearing Liquidity Bus.
 */
typedef enum b3_settlement_cycle {
    B3_CYCLE_INTRADAY_1         = 1,
    B3_CYCLE_INTRADAY_2         = 2,
    B3_CYCLE_MULTILATERAL_CLOSE = 3,
    B3_CYCLE_BILATERAL_EVENING  = 4
} b3_settlement_cycle_t;

/**
 * @brief Clearing Subsystems of B3 Clearinghouse.
 */
typedef enum b3_clearing_subsystem {
    B3_SUBSYSTEM_DERIVATIVES  = 1,
    B3_SUBSYSTEM_EQUITIES     = 2,
    B3_SUBSYSTEM_FIXED_INCOME = 3,
    B3_SUBSYSTEM_FX           = 4
} b3_clearing_subsystem_t;

/**
 * @brief Settlement Instruction Status.
 */
typedef enum b3_instruction_status {
    B3_STATUS_PENDING   = 0,
    B3_STATUS_SETTLED   = 1,
    B3_STATUS_CANCELLED = 2,
    B3_STATUS_REJECTED  = 3
} b3_instruction_status_t;

/* ========================================================================== */
/* PUMA SBE v2 Binary Wire Structures (Packed 1-byte)                         */
/* ========================================================================== */

#pragma pack(push, 1)

/**
 * @brief Canonical SBE Message Framing Header (8 bytes).
 */
typedef struct b3_sbe_header {
    uint16_t block_length;  /**< Root block length in bytes */
    uint16_t template_id;   /**< Message template ID */
    uint16_t schema_id;     /**< SBE schema identifier */
    uint16_t version;       /**< SBE schema version */
} b3_sbe_header_t;

/**
 * @brief New Order Single Binary Message (Template 101, 42 bytes total).
 */
typedef struct b3_new_order_single {
    b3_sbe_header_t header;         /**< Offset 0,  8 bytes */
    uint64_t        cl_ord_id;      /**< Offset 8,  8 bytes */
    uint64_t        security_id;    /**< Offset 16, 8 bytes */
    uint8_t         side;           /**< Offset 24, 1 byte  (1=Buy, 2=Sell) */
    uint32_t        order_qty;      /**< Offset 25, 4 bytes */
    int64_t         price_scaled;   /**< Offset 29, 8 bytes (Price * 10^8) */
    uint8_t         time_in_force;  /**< Offset 37, 1 byte  (0=Day, 1=GTC, etc.) */
    uint32_t        account;        /**< Offset 38, 4 bytes (Trading account) */
} b3_new_order_single_t;

/**
 * @brief Order Cancel Binary Message (Template 102, 36 bytes total).
 */
typedef struct b3_order_cancel {
    b3_sbe_header_t header;         /**< Offset 0,  8 bytes */
    uint64_t        cl_ord_id;      /**< Offset 8,  8 bytes */
    uint64_t        orig_cl_ord_id; /**< Offset 16, 8 bytes */
    uint64_t        security_id;    /**< Offset 24, 8 bytes */
    uint32_t        account;        /**< Offset 32, 4 bytes */
} b3_order_cancel_t;

/**
 * @brief Execution Report Binary Message (Template 103, 62 bytes total).
 */
typedef struct b3_execution_report {
    b3_sbe_header_t header;         /**< Offset 0,  8 bytes */
    uint64_t        order_id;       /**< Offset 8,  8 bytes */
    uint64_t        cl_ord_id;      /**< Offset 16, 8 bytes */
    uint64_t        exec_id;        /**< Offset 24, 8 bytes */
    uint8_t         exec_type;      /**< Offset 32, 1 byte  */
    uint8_t         ord_status;     /**< Offset 33, 1 byte  */
    uint64_t        security_id;    /**< Offset 34, 8 bytes */
    uint32_t        cum_qty;        /**< Offset 42, 4 bytes */
    uint32_t        leaves_qty;     /**< Offset 46, 4 bytes */
    int64_t         last_px_scaled; /**< Offset 50, 8 bytes (LastPx * 10^8) */
    uint32_t        last_qty;       /**< Offset 58, 4 bytes */
} b3_execution_report_t;

#pragma pack(pop)

/* ========================================================================== */
/* In-Memory HFT & Settlement Structures (Naturally Aligned)                  */
/* ========================================================================== */

/**
 * @brief HFT Order Buffer Entry aligned to 64 bytes (1 CPU Cache Line).
 */
typedef struct b3_order_buffer_entry {
    uint64_t entry_id;      /**< Offset 0,  8 bytes */
    uint64_t timestamp_ns;  /**< Offset 8,  8 bytes */
    uint64_t cl_ord_id;     /**< Offset 16, 8 bytes */
    uint64_t security_id;   /**< Offset 24, 8 bytes */
    int64_t  price_scaled;  /**< Offset 32, 8 bytes (Price * 10^8) */
    uint32_t quantity;      /**< Offset 40, 4 bytes */
    uint32_t account;       /**< Offset 44, 4 bytes */
    uint8_t  side;          /**< Offset 48, 1 byte  */
    uint8_t  time_in_force; /**< Offset 49, 1 byte  */
    uint8_t  order_status;  /**< Offset 50, 1 byte  */
    uint8_t  _pad[5];       /**< Offset 51, 5 bytes padding to align symbol */
    char     symbol[8];     /**< Offset 56, 8 bytes null-padded ticker */
} b3_order_buffer_entry_t;

/**
 * @brief Lockless Circular Ring Buffer Header (32 bytes).
 */
typedef struct b3_order_ring_buffer_header {
    uint32_t capacity;      /**< Ring buffer capacity (power of 2) */
    uint32_t mask;          /**< capacity - 1 */
    uint64_t head;          /**< Write sequence index */
    uint64_t tail;          /**< Read sequence index */
    uint32_t entry_size;    /**< Size of each entry (64 bytes) */
    uint32_t flags;         /**< Control flags */
} b3_order_ring_buffer_header_t;

/**
 * @brief B3 In-Memory Custody Position (104 bytes, 8-byte aligned).
 */
typedef struct b3_custody_position {
    uint32_t participant_id;        /**< Offset 0,  4 bytes */
    uint32_t _pad0;                 /**< Offset 4,  4 bytes */
    uint64_t account_id;            /**< Offset 8,  8 bytes */
    uint64_t security_id;           /**< Offset 16, 8 bytes */
    int64_t  available_qty;         /**< Offset 24, 8 bytes */
    int64_t  blocked_margin_qty;    /**< Offset 32, 8 bytes */
    int64_t  blocked_loan_qty;      /**< Offset 40, 8 bytes */
    int64_t  blocked_judicial_qty;  /**< Offset 48, 8 bytes */
    int64_t  total_qty;             /**< Offset 56, 8 bytes */
    uint64_t updated_at_ns;         /**< Offset 64, 8 bytes */
    char     symbol[16];            /**< Offset 72, 16 bytes */
    char     isin[12];              /**< Offset 88, 12 bytes */
    uint8_t  _pad1[4];              /**< Offset 100, 4 bytes padding */
} b3_custody_position_t;

/**
 * @brief Multilateral Clearing Transfer Record (72 bytes, 8-byte aligned).
 */
typedef struct b3_multilateral_transfer {
    uint64_t transfer_id;           /**< Offset 0,  8 bytes */
    uint64_t instruction_id;        /**< Offset 8,  8 bytes */
    uint32_t cycle_id;              /**< Offset 16, 4 bytes (b3_settlement_cycle_t) */
    uint32_t subsystem;             /**< Offset 20, 4 bytes (b3_clearing_subsystem_t) */
    uint32_t debtor_member_id;      /**< Offset 24, 4 bytes */
    uint32_t creditor_member_id;    /**< Offset 28, 4 bytes */
    int64_t  gross_amount_cents;    /**< Offset 32, 8 bytes (Amount * 100) */
    uint64_t asset_id;              /**< Offset 40, 8 bytes */
    int64_t  asset_quantity;        /**< Offset 48, 8 bytes */
    uint32_t status;                /**< Offset 56, 4 bytes (b3_instruction_status_t) */
    uint32_t _pad0;                 /**< Offset 60, 4 bytes */
    uint64_t timestamp_ns;          /**< Offset 64, 8 bytes */
} b3_multilateral_transfer_t;

/**
 * @brief Multilateral Net Position for Clearing Member (48 bytes, 8-byte aligned).
 */
typedef struct b3_multilateral_net_position {
    uint32_t member_id;                 /**< Offset 0,  4 bytes */
    uint32_t cycle_id;                  /**< Offset 4,  4 bytes */
    int64_t  gross_receivables_cents;   /**< Offset 8,  8 bytes */
    int64_t  gross_payables_cents;      /**< Offset 16, 8 bytes */
    int64_t  net_balance_cents;         /**< Offset 24, 8 bytes */
    uint32_t status;                    /**< Offset 32, 4 bytes */
    uint32_t _pad0;                     /**< Offset 36, 4 bytes */
    uint64_t updated_at_ns;             /**< Offset 40, 8 bytes */
} b3_multilateral_net_position_t;

/* ========================================================================== */
/* Compile-Time Size & Offset Verifications                                   */
/* ========================================================================== */

#if defined(__STDC_VERSION__) && __STDC_VERSION__ >= 201112L
_Static_assert(sizeof(b3_sbe_header_t) == 8, "b3_sbe_header_t must be 8 bytes");
_Static_assert(sizeof(b3_new_order_single_t) == 42, "b3_new_order_single_t must be 42 bytes");
_Static_assert(sizeof(b3_order_cancel_t) == 36, "b3_order_cancel_t must be 36 bytes");
_Static_assert(sizeof(b3_execution_report_t) == 62, "b3_execution_report_t must be 62 bytes");
_Static_assert(sizeof(b3_order_buffer_entry_t) == 64, "b3_order_buffer_entry_t must be 64 bytes");
_Static_assert(sizeof(b3_order_ring_buffer_header_t) == 32, "b3_order_ring_buffer_header_t must be 32 bytes");
_Static_assert(sizeof(b3_custody_position_t) == 104, "b3_custody_position_t must be 104 bytes");
_Static_assert(sizeof(b3_multilateral_transfer_t) == 72, "b3_multilateral_transfer_t must be 72 bytes");
_Static_assert(sizeof(b3_multilateral_net_position_t) == 48, "b3_multilateral_net_position_t must be 48 bytes");
#endif

/* ========================================================================== */
/* Function Prototypes & Zero-Copy FFI Signatures                             */
/* ========================================================================== */

/**
 * @brief Encodes New Order Single directly into binary buffer.
 */
int b3_sbe_encode_new_order(
    const b3_new_order_single_t* order,
    uint8_t* out_buf,
    size_t out_len
);

/**
 * @brief Decodes New Order Single zero-copy from input buffer.
 */
int b3_sbe_decode_new_order(
    const uint8_t* in_buf,
    size_t in_len,
    b3_new_order_single_t* out_order
);

/**
 * @brief Encodes Order Cancel message.
 */
int b3_sbe_encode_order_cancel(
    const b3_order_cancel_t* cancel,
    uint8_t* out_buf,
    size_t out_len
);

/**
 * @brief Decodes Order Cancel message.
 */
int b3_sbe_decode_order_cancel(
    const uint8_t* in_buf,
    size_t in_len,
    b3_order_cancel_t* out_cancel
);

/**
 * @brief Encodes Execution Report message.
 */
int b3_sbe_encode_execution_report(
    const b3_execution_report_t* report,
    uint8_t* out_buf,
    size_t out_len
);

/**
 * @brief Decodes Execution Report message.
 */
int b3_sbe_decode_execution_report(
    const uint8_t* in_buf,
    size_t in_len,
    b3_execution_report_t* out_report
);

/**
 * @brief Verifies canonical custody equation: Total == Available + All Blocked.
 * @return true if valid, false otherwise.
 */
bool b3_custody_position_is_consistent(
    const b3_custody_position_t* pos
);

/**
 * @brief Computes multilateral net balance: receivables - payables.
 */
int64_t b3_multilateral_compute_net(
    int64_t gross_receivables_cents,
    int64_t gross_payables_cents
);

/**
 * @brief Pushes an order into circular ring buffer.
 */
int b3_order_ring_buffer_push(
    b3_order_ring_buffer_header_t* header,
    b3_order_buffer_entry_t* entries,
    const b3_order_buffer_entry_t* item
);

#ifdef __cplusplus
}
#endif

#endif /* B3_ATLAS_C_ABI_H */
