//! B3 Integration Atlas — High-Performance C-ABI & Rust Interop FFI Layer
//!
//! Provides zero-copy `#[repr(C)]` layouts, packed wire-format SBE structs,
//! cache-line aligned order buffers, custody positions, and multilateral clearing.

#![allow(non_camel_case_types)]
#![allow(dead_code)]

use core::mem::size_of;
use core::slice;

pub const B3_PRICE_SCALE: i64 = 100_000_000;
pub const B3_CENT_SCALE: i64 = 100;
pub const B3_CACHE_LINE_SIZE: usize = 64;

pub const B3_DEFAULT_SCHEMA_ID: u16 = 1;
pub const B3_DEFAULT_SCHEMA_VERSION: u16 = 1;

pub const B3_TEMPLATE_NEW_ORDER: u16 = 101;
pub const B3_TEMPLATE_ORDER_CANCEL: u16 = 102;
pub const B3_TEMPLATE_EXECUTION_REPORT: u16 = 103;

pub const B3_OK: i32 = 0;
pub const B3_ERR_NULL_POINTER: i32 = -1;
pub const B3_ERR_BUFFER_TOO_SMALL: i32 = -2;
pub const B3_ERR_INVALID_TEMPLATE: i32 = -3;
pub const B3_ERR_INVALID_BLOCK_LENGTH: i32 = -4;
pub const B3_ERR_VALIDATION_FAILED: i32 = -5;

// ==============================================================================
// Enumerations
// ==============================================================================

#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum B3Side {
    Buy = 1,
    Sell = 2,
}

#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum B3TimeInForce {
    Day = 0,
    Gtc = 1,
    Ioc = 3,
    Fok = 4,
}

#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum B3ExecType {
    New = 0,
    PartialFill = 1,
    Fill = 2,
    Cancelled = 4,
    Rejected = 8,
}

#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum B3OrdStatus {
    New = 0,
    PartiallyFilled = 1,
    Filled = 2,
    Cancelled = 4,
    Rejected = 8,
}

#[repr(u32)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum B3SettlementCycle {
    Intraday1 = 1,
    Intraday2 = 2,
    MultilateralClose = 3,
    BilateralEvening = 4,
}

#[repr(u32)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum B3ClearingSubsystem {
    Derivatives = 1,
    Equities = 2,
    FixedIncome = 3,
    Fx = 4,
}

#[repr(u32)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum B3InstructionStatus {
    Pending = 0,
    Settled = 1,
    Cancelled = 2,
    Rejected = 3,
}

// ==============================================================================
// PUMA SBE v2 Binary Wire Structures (Packed 1-byte)
// ==============================================================================

#[repr(C, packed)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct B3SbeHeader {
    pub block_length: u16,
    pub template_id: u16,
    pub schema_id: u16,
    pub version: u16,
}

impl B3SbeHeader {
    pub const SIZE: usize = size_of::<Self>();

    pub fn new(block_length: u16, template_id: u16) -> Self {
        Self {
            block_length,
            template_id,
            schema_id: B3_DEFAULT_SCHEMA_ID,
            version: B3_DEFAULT_SCHEMA_VERSION,
        }
    }
}

#[repr(C, packed)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct B3NewOrderSingle {
    pub header: B3SbeHeader,
    pub cl_ord_id: u64,
    pub security_id: u64,
    pub side: u8,
    pub order_qty: u32,
    pub price_scaled: i64,
    pub time_in_force: u8,
    pub account: u32,
}

impl B3NewOrderSingle {
    pub const TEMPLATE_ID: u16 = B3_TEMPLATE_NEW_ORDER;
    pub const BLOCK_LENGTH: u16 = 34;
    pub const SIZE: usize = size_of::<Self>(); // 42 bytes

    pub fn new(
        cl_ord_id: u64,
        security_id: u64,
        side: u8,
        order_qty: u32,
        price: f64,
        time_in_force: u8,
        account: u32,
    ) -> Self {
        Self {
            header: B3SbeHeader::new(Self::BLOCK_LENGTH, Self::TEMPLATE_ID),
            cl_ord_id,
            security_id,
            side,
            order_qty,
            price_scaled: (price * B3_PRICE_SCALE as f64).round() as i64,
            time_in_force,
            account,
        }
    }

    pub fn price(&self) -> f64 {
        self.price_scaled as f64 / B3_PRICE_SCALE as f64
    }

    pub fn set_price(&mut self, price: f64) {
        self.price_scaled = (price * B3_PRICE_SCALE as f64).round() as i64;
    }

    pub fn as_bytes(&self) -> &[u8] {
        unsafe { slice::from_raw_parts((self as *const Self) as *const u8, Self::SIZE) }
    }
}

#[repr(C, packed)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct B3OrderCancel {
    pub header: B3SbeHeader,
    pub cl_ord_id: u64,
    pub orig_cl_ord_id: u64,
    pub security_id: u64,
    pub account: u32,
}

impl B3OrderCancel {
    pub const TEMPLATE_ID: u16 = B3_TEMPLATE_ORDER_CANCEL;
    pub const BLOCK_LENGTH: u16 = 28;
    pub const SIZE: usize = size_of::<Self>(); // 36 bytes

    pub fn new(cl_ord_id: u64, orig_cl_ord_id: u64, security_id: u64, account: u32) -> Self {
        Self {
            header: B3SbeHeader::new(Self::BLOCK_LENGTH, Self::TEMPLATE_ID),
            cl_ord_id,
            orig_cl_ord_id,
            security_id,
            account,
        }
    }

    pub fn as_bytes(&self) -> &[u8] {
        unsafe { slice::from_raw_parts((self as *const Self) as *const u8, Self::SIZE) }
    }
}

#[repr(C, packed)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct B3ExecutionReport {
    pub header: B3SbeHeader,
    pub order_id: u64,
    pub cl_ord_id: u64,
    pub exec_id: u64,
    pub exec_type: u8,
    pub ord_status: u8,
    pub security_id: u64,
    pub cum_qty: u32,
    pub leaves_qty: u32,
    pub last_px_scaled: i64,
    pub last_qty: u32,
}

impl B3ExecutionReport {
    pub const TEMPLATE_ID: u16 = B3_TEMPLATE_EXECUTION_REPORT;
    pub const BLOCK_LENGTH: u16 = 54;
    pub const SIZE: usize = size_of::<Self>(); // 62 bytes

    pub fn last_px(&self) -> f64 {
        self.last_px_scaled as f64 / B3_PRICE_SCALE as f64
    }

    pub fn set_last_px(&mut self, px: f64) {
        self.last_px_scaled = (px * B3_PRICE_SCALE as f64).round() as i64;
    }

    pub fn as_bytes(&self) -> &[u8] {
        unsafe { slice::from_raw_parts((self as *const Self) as *const u8, Self::SIZE) }
    }
}

// ==============================================================================
// In-Memory HFT & Settlement Structures (Naturally Aligned)
// ==============================================================================

#[repr(C, align(8))]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct B3OrderBufferEntry {
    pub entry_id: u64,
    pub timestamp_ns: u64,
    pub cl_ord_id: u64,
    pub security_id: u64,
    pub price_scaled: i64,
    pub quantity: u32,
    pub account: u32,
    pub side: u8,
    pub time_in_force: u8,
    pub order_status: u8,
    pub _pad: [u8; 5],
    pub symbol: [u8; 8],
}

impl B3OrderBufferEntry {
    pub const SIZE: usize = size_of::<Self>(); // 64 bytes (1 cache line)

    pub fn price(&self) -> f64 {
        self.price_scaled as f64 / B3_PRICE_SCALE as f64
    }

    pub fn set_price(&mut self, price: f64) {
        self.price_scaled = (price * B3_PRICE_SCALE as f64).round() as i64;
    }

    pub fn set_symbol(&mut self, s: &str) {
        self.symbol = [0u8; 8];
        let bytes = s.as_bytes();
        let len = bytes.len().min(8);
        self.symbol[..len].copy_from_slice(&bytes[..len]);
    }
}

#[repr(C, align(8))]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct B3OrderRingBufferHeader {
    pub capacity: u32,
    pub mask: u32,
    pub head: u64,
    pub tail: u64,
    pub entry_size: u32,
    pub flags: u32,
}

#[repr(C, align(8))]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct B3CustodyPosition {
    pub participant_id: u32,
    pub _pad0: u32,
    pub account_id: u64,
    pub security_id: u64,
    pub available_qty: i64,
    pub blocked_margin_qty: i64,
    pub blocked_loan_qty: i64,
    pub blocked_judicial_qty: i64,
    pub total_qty: i64,
    pub updated_at_ns: u64,
    pub symbol: [u8; 16],
    pub isin: [u8; 12],
    pub _pad1: [u8; 4],
}

impl B3CustodyPosition {
    pub const SIZE: usize = size_of::<Self>(); // 104 bytes

    /// Canonical Invariant: Total == Available + Sum(Blocked)
    pub fn is_consistent(&self) -> bool {
        let expected_total = self.available_qty
            + self.blocked_margin_qty
            + self.blocked_loan_qty
            + self.blocked_judicial_qty;
        self.total_qty == expected_total
    }

    pub fn recalculate_total(&mut self) -> i64 {
        self.total_qty = self.available_qty
            + self.blocked_margin_qty
            + self.blocked_loan_qty
            + self.blocked_judicial_qty;
        self.total_qty
    }
}

#[repr(C, align(8))]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct B3MultilateralTransfer {
    pub transfer_id: u64,
    pub instruction_id: u64,
    pub cycle_id: u32,
    pub subsystem: u32,
    pub debtor_member_id: u32,
    pub creditor_member_id: u32,
    pub gross_amount_cents: i64,
    pub asset_id: u64,
    pub asset_quantity: i64,
    pub status: u32,
    pub _pad0: u32,
    pub timestamp_ns: u64,
}

impl B3MultilateralTransfer {
    pub const SIZE: usize = size_of::<Self>(); // 72 bytes

    pub fn gross_amount(&self) -> f64 {
        self.gross_amount_cents as f64 / B3_CENT_SCALE as f64
    }

    pub fn set_gross_amount(&mut self, amt: f64) {
        self.gross_amount_cents = (amt * B3_CENT_SCALE as f64).round() as i64;
    }
}

#[repr(C, align(8))]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct B3MultilateralNetPosition {
    pub member_id: u32,
    pub cycle_id: u32,
    pub gross_receivables_cents: i64,
    pub gross_payables_cents: i64,
    pub net_balance_cents: i64,
    pub status: u32,
    pub _pad0: u32,
    pub updated_at_ns: u64,
}

impl B3MultilateralNetPosition {
    pub const SIZE: usize = size_of::<Self>(); // 48 bytes

    pub fn compute_net_balance(&mut self) -> i64 {
        self.net_balance_cents = self.gross_receivables_cents - self.gross_payables_cents;
        self.net_balance_cents
    }

    pub fn is_net_balanced(&self) -> bool {
        self.net_balance_cents == (self.gross_receivables_cents - self.gross_payables_cents)
    }
}

// ==============================================================================
// Exported C-ABI Functions
// ==============================================================================

#[no_mangle]
pub unsafe extern "C" fn b3_sbe_encode_new_order(
    order: *const B3NewOrderSingle,
    out_buf: *mut u8,
    out_len: usize,
) -> i32 {
    if order.is_null() || out_buf.is_null() {
        return B3_ERR_NULL_POINTER;
    }
    if out_len < B3NewOrderSingle::SIZE {
        return B3_ERR_BUFFER_TOO_SMALL;
    }
    core::ptr::copy_nonoverlapping(
        order as *const u8,
        out_buf,
        B3NewOrderSingle::SIZE,
    );
    B3_OK
}

#[no_mangle]
pub unsafe extern "C" fn b3_sbe_decode_new_order(
    in_buf: *const u8,
    in_len: usize,
    out_order: *mut B3NewOrderSingle,
) -> i32 {
    if in_buf.is_null() || out_order.is_null() {
        return B3_ERR_NULL_POINTER;
    }
    if in_len < B3NewOrderSingle::SIZE {
        return B3_ERR_BUFFER_TOO_SMALL;
    }
    let header = &*(in_buf as *const B3SbeHeader);
    if header.template_id != B3_TEMPLATE_NEW_ORDER {
        return B3_ERR_INVALID_TEMPLATE;
    }
    if header.block_length != B3NewOrderSingle::BLOCK_LENGTH {
        return B3_ERR_INVALID_BLOCK_LENGTH;
    }
    core::ptr::copy_nonoverlapping(
        in_buf,
        out_order as *mut u8,
        B3NewOrderSingle::SIZE,
    );
    B3_OK
}

#[no_mangle]
pub unsafe extern "C" fn b3_sbe_encode_order_cancel(
    cancel: *const B3OrderCancel,
    out_buf: *mut u8,
    out_len: usize,
) -> i32 {
    if cancel.is_null() || out_buf.is_null() {
        return B3_ERR_NULL_POINTER;
    }
    if out_len < B3OrderCancel::SIZE {
        return B3_ERR_BUFFER_TOO_SMALL;
    }
    core::ptr::copy_nonoverlapping(
        cancel as *const u8,
        out_buf,
        B3OrderCancel::SIZE,
    );
    B3_OK
}

#[no_mangle]
pub unsafe extern "C" fn b3_sbe_decode_order_cancel(
    in_buf: *const u8,
    in_len: usize,
    out_cancel: *mut B3OrderCancel,
) -> i32 {
    if in_buf.is_null() || out_cancel.is_null() {
        return B3_ERR_NULL_POINTER;
    }
    if in_len < B3OrderCancel::SIZE {
        return B3_ERR_BUFFER_TOO_SMALL;
    }
    let header = &*(in_buf as *const B3SbeHeader);
    if header.template_id != B3_TEMPLATE_ORDER_CANCEL {
        return B3_ERR_INVALID_TEMPLATE;
    }
    if header.block_length != B3OrderCancel::BLOCK_LENGTH {
        return B3_ERR_INVALID_BLOCK_LENGTH;
    }
    core::ptr::copy_nonoverlapping(
        in_buf,
        out_cancel as *mut u8,
        B3OrderCancel::SIZE,
    );
    B3_OK
}

#[no_mangle]
pub unsafe extern "C" fn b3_sbe_encode_execution_report(
    report: *const B3ExecutionReport,
    out_buf: *mut u8,
    out_len: usize,
) -> i32 {
    if report.is_null() || out_buf.is_null() {
        return B3_ERR_NULL_POINTER;
    }
    if out_len < B3ExecutionReport::SIZE {
        return B3_ERR_BUFFER_TOO_SMALL;
    }
    core::ptr::copy_nonoverlapping(
        report as *const u8,
        out_buf,
        B3ExecutionReport::SIZE,
    );
    B3_OK
}

#[no_mangle]
pub unsafe extern "C" fn b3_sbe_decode_execution_report(
    in_buf: *const u8,
    in_len: usize,
    out_report: *mut B3ExecutionReport,
) -> i32 {
    if in_buf.is_null() || out_report.is_null() {
        return B3_ERR_NULL_POINTER;
    }
    if in_len < B3ExecutionReport::SIZE {
        return B3_ERR_BUFFER_TOO_SMALL;
    }
    let header = &*(in_buf as *const B3SbeHeader);
    if header.template_id != B3_TEMPLATE_EXECUTION_REPORT {
        return B3_ERR_INVALID_TEMPLATE;
    }
    if header.block_length != B3ExecutionReport::BLOCK_LENGTH {
        return B3_ERR_INVALID_BLOCK_LENGTH;
    }
    core::ptr::copy_nonoverlapping(
        in_buf,
        out_report as *mut u8,
        B3ExecutionReport::SIZE,
    );
    B3_OK
}

#[no_mangle]
pub unsafe extern "C" fn b3_custody_position_is_consistent(
    pos: *const B3CustodyPosition,
) -> bool {
    if pos.is_null() {
        return false;
    }
    (*pos).is_consistent()
}

#[no_mangle]
pub extern "C" fn b3_multilateral_compute_net(
    gross_receivables_cents: i64,
    gross_payables_cents: i64,
) -> i64 {
    gross_receivables_cents - gross_payables_cents
}

#[no_mangle]
pub unsafe extern "C" fn b3_order_ring_buffer_push(
    header: *mut B3OrderRingBufferHeader,
    entries: *mut B3OrderBufferEntry,
    item: *const B3OrderBufferEntry,
) -> i32 {
    if header.is_null() || entries.is_null() || item.is_null() {
        return B3_ERR_NULL_POINTER;
    }
    let head = (*header).head;
    let slot = (head & (*header).mask as u64) as usize;
    let target = entries.add(slot);
    core::ptr::copy_nonoverlapping(item, target, 1);
    (*header).head = head + 1;
    B3_OK
}
