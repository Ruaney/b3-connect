/**
 * @file b3_atlas_c_abi.c
 * @brief B3 Integration Atlas - High-Performance Native C11 Implementation
 *
 * Implements the zero-copy SBE v2 binary codecs, custody position conservation
 * checks (LAW-008), multilateral clearing netting (LAW-106A), and lockless
 * HFT order ring buffer operations declared in b3_atlas_c_abi.h.
 */

#include "b3_atlas_c_abi.h"
#include <string.h>

int b3_sbe_encode_new_order(
    const b3_new_order_single_t* order,
    uint8_t* out_buf,
    size_t out_len
) {
    if (!order || !out_buf) {
        return B3_ERR_NULL_POINTER;
    }
    if (out_len < sizeof(b3_new_order_single_t)) {
        return B3_ERR_BUFFER_TOO_SMALL;
    }
    if (order->header.template_id != B3_TEMPLATE_NEW_ORDER) {
        return B3_ERR_INVALID_TEMPLATE;
    }
    if (order->header.block_length != (sizeof(b3_new_order_single_t) - sizeof(b3_sbe_header_t))) {
        return B3_ERR_INVALID_BLOCK_LENGTH;
    }
    memcpy(out_buf, order, sizeof(b3_new_order_single_t));
    return (int)sizeof(b3_new_order_single_t);
}

int b3_sbe_decode_new_order(
    const uint8_t* in_buf,
    size_t in_len,
    b3_new_order_single_t* out_order
) {
    if (!in_buf || !out_order) {
        return B3_ERR_NULL_POINTER;
    }
    if (in_len < sizeof(b3_new_order_single_t)) {
        return B3_ERR_BUFFER_TOO_SMALL;
    }
    memcpy(out_order, in_buf, sizeof(b3_new_order_single_t));
    if (out_order->header.template_id != B3_TEMPLATE_NEW_ORDER) {
        return B3_ERR_INVALID_TEMPLATE;
    }
    if (out_order->header.block_length != (sizeof(b3_new_order_single_t) - sizeof(b3_sbe_header_t))) {
        return B3_ERR_INVALID_BLOCK_LENGTH;
    }
    return B3_OK;
}

int b3_sbe_encode_order_cancel(
    const b3_order_cancel_t* cancel,
    uint8_t* out_buf,
    size_t out_len
) {
    if (!cancel || !out_buf) {
        return B3_ERR_NULL_POINTER;
    }
    if (out_len < sizeof(b3_order_cancel_t)) {
        return B3_ERR_BUFFER_TOO_SMALL;
    }
    if (cancel->header.template_id != B3_TEMPLATE_ORDER_CANCEL) {
        return B3_ERR_INVALID_TEMPLATE;
    }
    if (cancel->header.block_length != (sizeof(b3_order_cancel_t) - sizeof(b3_sbe_header_t))) {
        return B3_ERR_INVALID_BLOCK_LENGTH;
    }
    memcpy(out_buf, cancel, sizeof(b3_order_cancel_t));
    return (int)sizeof(b3_order_cancel_t);
}

int b3_sbe_decode_order_cancel(
    const uint8_t* in_buf,
    size_t in_len,
    b3_order_cancel_t* out_cancel
) {
    if (!in_buf || !out_cancel) {
        return B3_ERR_NULL_POINTER;
    }
    if (in_len < sizeof(b3_order_cancel_t)) {
        return B3_ERR_BUFFER_TOO_SMALL;
    }
    memcpy(out_cancel, in_buf, sizeof(b3_order_cancel_t));
    if (out_cancel->header.template_id != B3_TEMPLATE_ORDER_CANCEL) {
        return B3_ERR_INVALID_TEMPLATE;
    }
    if (out_cancel->header.block_length != (sizeof(b3_order_cancel_t) - sizeof(b3_sbe_header_t))) {
        return B3_ERR_INVALID_BLOCK_LENGTH;
    }
    return B3_OK;
}

int b3_sbe_encode_execution_report(
    const b3_execution_report_t* report,
    uint8_t* out_buf,
    size_t out_len
) {
    if (!report || !out_buf) {
        return B3_ERR_NULL_POINTER;
    }
    if (out_len < sizeof(b3_execution_report_t)) {
        return B3_ERR_BUFFER_TOO_SMALL;
    }
    if (report->header.template_id != B3_TEMPLATE_EXECUTION_REPORT) {
        return B3_ERR_INVALID_TEMPLATE;
    }
    if (report->header.block_length != (sizeof(b3_execution_report_t) - sizeof(b3_sbe_header_t))) {
        return B3_ERR_INVALID_BLOCK_LENGTH;
    }
    memcpy(out_buf, report, sizeof(b3_execution_report_t));
    return (int)sizeof(b3_execution_report_t);
}

int b3_sbe_decode_execution_report(
    const uint8_t* in_buf,
    size_t in_len,
    b3_execution_report_t* out_report
) {
    if (!in_buf || !out_report) {
        return B3_ERR_NULL_POINTER;
    }
    if (in_len < sizeof(b3_execution_report_t)) {
        return B3_ERR_BUFFER_TOO_SMALL;
    }
    memcpy(out_report, in_buf, sizeof(b3_execution_report_t));
    if (out_report->header.template_id != B3_TEMPLATE_EXECUTION_REPORT) {
        return B3_ERR_INVALID_TEMPLATE;
    }
    if (out_report->header.block_length != (sizeof(b3_execution_report_t) - sizeof(b3_sbe_header_t))) {
        return B3_ERR_INVALID_BLOCK_LENGTH;
    }
    return B3_OK;
}

bool b3_custody_position_is_consistent(
    const b3_custody_position_t* pos
) {
    if (!pos) {
        return false;
    }
    if (pos->available_qty < 0 ||
        pos->blocked_margin_qty < 0 ||
        pos->blocked_loan_qty < 0 ||
        pos->blocked_judicial_qty < 0) {
        return false;
    }
    int64_t expected_total = pos->available_qty
                           + pos->blocked_margin_qty
                           + pos->blocked_loan_qty
                           + pos->blocked_judicial_qty;
    return pos->total_qty == expected_total;
}

int64_t b3_multilateral_compute_net(
    int64_t gross_receivables_cents,
    int64_t gross_payables_cents
) {
    return gross_receivables_cents - gross_payables_cents;
}

int b3_order_ring_buffer_push(
    b3_order_ring_buffer_header_t* header,
    b3_order_buffer_entry_t* entries,
    const b3_order_buffer_entry_t* item
) {
    if (!header || !entries || !item) {
        return B3_ERR_NULL_POINTER;
    }
    if (header->capacity == 0 || (header->capacity & (header->capacity - 1)) != 0) {
        return B3_ERR_VALIDATION_FAILED;
    }
    if ((header->head - header->tail) >= (uint64_t)header->capacity) {
        return B3_ERR_BUFFER_TOO_SMALL;
    }
    uint64_t slot = header->head & (uint64_t)header->mask;
    entries[slot] = *item;
    header->head += 1;
    return B3_OK;
}
