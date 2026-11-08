"""Trading, Orders & Closing Auction Schemas for OpenAPI 3.1."""

def get_trading_schemas():
    return {
        "OrderSideEnum": {
            "type": "string",
            "enum": ["BUY", "SELL"],
            "example": "BUY"
        },
        "OrderTypeEnum": {
            "type": "string",
            "enum": ["MARKET", "LIMIT", "STOP_LIMIT", "ICEBERG", "MOC", "LOC", "TWAP", "VWAP"],
            "example": "LIMIT"
        },
        "TimeInForceEnum": {
            "type": "string",
            "enum": ["DAY", "IOC", "FOK", "GTC", "GTD", "AT_THE_CLOSE"],
            "example": "DAY"
        },
        "OrderStatusEnum": {
            "type": "string",
            "enum": ["NEW", "PARTIALLY_FILLED", "FILLED", "CANCELLED", "REJECTED", "EXPIRED", "FROZEN_IN_AUCTION"],
            "example": "NEW"
        },
        "PreTradeRiskCheckResult": {
            "type": "object",
            "required": ["passed", "fat_finger_limit_passed", "price_tunnel_passed", "timestamp"],
            "properties": {
                "passed": {"type": "boolean", "example": True},
                "fat_finger_limit_passed": {"type": "boolean", "example": True},
                "gross_notional_limit_passed": {"type": "boolean", "example": True},
                "price_tunnel_passed": {"type": "boolean", "example": True},
                "order_rate_limit_passed": {"type": "boolean", "example": True},
                "timestamp": {"type": "string", "format": "date-time", "example": "2026-11-08T02:45:00Z"},
                "governance_rule": {"type": "string", "example": "Instrução CVM nº 505 / Manual de Riscos B3 (LAW-001)"}
            }
        },
        "OrderCreateRequest": {
            "type": "object",
            "required": ["client_order_id", "account", "investor_id", "symbol", "side", "order_type", "quantity", "time_in_force"],
            "properties": {
                "client_order_id": {"type": "string", "example": "CLORD-20261108-001"},
                "account": {"type": "string", "example": "100982-1"},
                "investor_id": {"type": "string", "example": "01234567890"},
                "symbol": {"type": "string", "pattern": "^[A-Z0-9]{4,8}$", "example": "PETR4"},
                "side": {"$ref": "#/components/schemas/OrderSideEnum"},
                "order_type": {"$ref": "#/components/schemas/OrderTypeEnum"},
                "quantity": {"type": "integer", "minimum": 1, "example": 1000},
                "price": {"type": "number", "minimum": 0.01, "example": 38.45},
                "stop_price": {"type": "number", "minimum": 0.01, "example": 37.90},
                "display_qty": {"type": "integer", "example": 100},
                "time_in_force": {"$ref": "#/components/schemas/TimeInForceEnum"},
                "expire_date": {"type": "string", "format": "date", "example": "2026-11-30"},
                "memo": {"type": "string", "example": "SOR-REBALANCE-Q4"}
            }
        },
        "OrderFill": {
            "type": "object",
            "required": ["fill_id", "match_exec_id", "price", "quantity", "timestamp"],
            "properties": {
                "fill_id": {"type": "string", "example": "FILL-998811"},
                "match_exec_id": {"type": "string", "example": "EXEC-PUMA-0012398"},
                "price": {"type": "string", "example": "38.45"},
                "quantity": {"type": "integer", "example": 500},
                "timestamp": {"type": "string", "format": "date-time", "example": "2026-11-08T02:45:00.123Z"},
                "counterparty_clearing_member": {"type": "string", "example": "0088"},
                "fee_brl": {"type": "string", "example": "5.76"}
            }
        },
        "OrderResponse": {
            "type": "object",
            "required": ["order_id", "client_order_id", "account", "investor_id", "symbol", "side", "order_type", "quantity", "executed_quantity", "leaves_quantity", "status", "time_in_force", "created_at", "pre_trade_risk"],
            "properties": {
                "order_id": {"type": "string", "example": "ORD-20261108-0010928"},
                "client_order_id": {"type": "string", "example": "CLORD-20261108-001"},
                "account": {"type": "string", "example": "100982-1"},
                "investor_id": {"type": "string", "example": "01234567890"},
                "symbol": {"type": "string", "example": "PETR4"},
                "side": {"$ref": "#/components/schemas/OrderSideEnum"},
                "order_type": {"$ref": "#/components/schemas/OrderTypeEnum"},
                "quantity": {"type": "integer", "example": 1000},
                "executed_quantity": {"type": "integer", "example": 500},
                "leaves_quantity": {"type": "integer", "example": 500},
                "price": {"type": "string", "example": "38.45"},
                "avg_executed_price": {"type": "string", "example": "38.45"},
                "status": {"$ref": "#/components/schemas/OrderStatusEnum"},
                "time_in_force": {"$ref": "#/components/schemas/TimeInForceEnum"},
                "created_at": {"type": "string", "format": "date-time", "example": "2026-11-08T02:45:00.100Z"},
                "updated_at": {"type": "string", "format": "date-time", "example": "2026-11-08T02:45:00.123Z"},
                "pre_trade_risk": {"$ref": "#/components/schemas/PreTradeRiskCheckResult"},
                "fills": {"type": "array", "items": {"$ref": "#/components/schemas/OrderFill"}}
            }
        },
        "OrderCancelResponse": {
            "type": "object",
            "required": ["order_id", "client_order_id", "symbol", "cancelled_quantity", "status", "timestamp"],
            "properties": {
                "order_id": {"type": "string", "example": "ORD-20261108-0010928"},
                "client_order_id": {"type": "string", "example": "CLORD-20261108-001"},
                "symbol": {"type": "string", "example": "PETR4"},
                "cancelled_quantity": {"type": "integer", "example": 500},
                "status": {"type": "string", "example": "CANCELLED"},
                "timestamp": {"type": "string", "format": "date-time", "example": "2026-11-08T02:46:10Z"}
            }
        },
        "ClosingAuctionPhaseEnum": {
            "type": "string",
            "enum": ["CALL_PHASE", "FREEZE_WINDOW", "RANDOM_EXTENSION", "UNCROSS", "CLOSED"],
            "example": "FREEZE_WINDOW"
        },
        "MocLocOrderCreateRequest": {
            "type": "object",
            "required": ["client_order_id", "account", "investor_id", "symbol", "side", "order_type", "quantity"],
            "properties": {
                "client_order_id": {"type": "string", "example": "MOC-20261108-0099"},
                "account": {"type": "string", "example": "100982-1"},
                "investor_id": {"type": "string", "example": "01234567890"},
                "symbol": {"type": "string", "example": "VALE3"},
                "side": {"$ref": "#/components/schemas/OrderSideEnum"},
                "order_type": {"type": "string", "enum": ["MOC", "LOC"], "example": "MOC"},
                "quantity": {"type": "integer", "minimum": 1, "example": 50000},
                "limit_price": {"type": "number", "minimum": 0.01, "example": 62.50}
            }
        },
        "MocLocOrderResponse": {
            "type": "object",
            "required": ["order_id", "client_order_id", "symbol", "side", "order_type", "quantity", "executed_quantity", "residual_quantity", "status", "freeze_window_protected", "priority_rank", "submitted_at"],
            "properties": {
                "order_id": {"type": "string", "example": "AUCT-ORD-001928"},
                "client_order_id": {"type": "string", "example": "MOC-20261108-0099"},
                "symbol": {"type": "string", "example": "VALE3"},
                "side": {"$ref": "#/components/schemas/OrderSideEnum"},
                "order_type": {"type": "string", "enum": ["MOC", "LOC"], "example": "MOC"},
                "quantity": {"type": "integer", "example": 50000},
                "executed_quantity": {"type": "integer", "example": 0},
                "residual_quantity": {"type": "integer", "example": 50000},
                "limit_price": {"type": "string", "example": "MARKET"},
                "status": {"type": "string", "example": "NEW"},
                "freeze_window_protected": {"type": "boolean", "example": True},
                "priority_rank": {"type": "integer", "example": 1},
                "submitted_at": {"type": "string", "format": "date-time", "example": "2026-11-08T16:56:00Z"}
            }
        },
        "TheoreticalPriceResponse": {
            "type": "object",
            "required": ["symbol", "timestamp", "auction_phase", "is_freeze_window_active", "theoretical_price", "projected_volume", "imbalance_side", "imbalance_volume", "price_tunnel_lower", "price_tunnel_upper"],
            "properties": {
                "symbol": {"type": "string", "example": "VALE3"},
                "timestamp": {"type": "string", "format": "date-time", "example": "2026-11-08T16:58:30Z"},
                "auction_phase": {"$ref": "#/components/schemas/ClosingAuctionPhaseEnum"},
                "is_freeze_window_active": {"type": "boolean", "example": True},
                "theoretical_price": {"type": "string", "example": "62.45"},
                "projected_volume": {"type": "integer", "example": 1250000},
                "imbalance_side": {"type": "string", "enum": ["BUY", "SELL", "BALANCED"], "example": "BUY"},
                "imbalance_volume": {"type": "integer", "example": 80000},
                "total_moc_buy_volume": {"type": "integer", "example": 750000},
                "total_moc_sell_volume": {"type": "integer", "example": 670000},
                "total_loc_buy_volume": {"type": "integer", "example": 500000},
                "total_loc_sell_volume": {"type": "integer", "example": 580000},
                "price_tunnel_lower": {"type": "string", "example": "61.20"},
                "price_tunnel_upper": {"type": "string", "example": "63.70"}
            }
        },
        "UncrossSimulationRequest": {
            "type": "object",
            "required": ["symbol"],
            "properties": {
                "symbol": {"type": "string", "example": "VALE3"},
                "force_uncross": {"type": "boolean", "example": False}
            }
        },
        "UncrossSimulationResult": {
            "type": "object",
            "required": ["symbol", "uncross_timestamp", "clearing_price", "total_volume_executed", "buy_orders_matched", "sell_orders_matched", "conservation_law_104c_verified"],
            "properties": {
                "symbol": {"type": "string", "example": "VALE3"},
                "uncross_timestamp": {"type": "string", "format": "date-time", "example": "2026-11-08T17:00:00Z"},
                "clearing_price": {"type": "string", "example": "62.45"},
                "total_volume_executed": {"type": "integer", "example": 1250000},
                "buy_orders_matched": {"type": "integer", "example": 42},
                "sell_orders_matched": {"type": "integer", "example": 38},
                "conservation_law_104c_verified": {"type": "boolean", "example": True}
            }
        }
    }
