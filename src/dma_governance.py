"""Módulo de Governança de Provedores de DMA e Filtros Pré-Trade B3 (FL-045).

Implementa a governança dos quatro modelos de Direct Market Access (DMA 1, DMA 2,
DMA 3 e DMA 4) e a bateria determinística de filtros pré-trade mandatórios pela
Resolução CVM nº 35/2021 (Art. 3º) e pelos Manuais de Risco e Procedimentos da B3.

Modelos de DMA:
- DMA 1: Tradicional (Broker Infrastructure - Home Broker, Web, Desktop)
- DMA 2: Via Algoritmo (Broker Algo - TWAP, VWAP, Snipers na Corretora)
- DMA 3: Direct to Exchange (Acesso Direto com Gateway de Risco Inline da Corretora)
- DMA 4: Co-location Santana de Parnaíba (Ultrabaixa Latência / SBE no Data Center B3)

Filtros Pré-Trade:
1. Trava de Kill-Switch Mandatório (Canal Bloqueado e Cancelamento Massivo < 100ms)
2. Throttling de Mensagens (Sliding Window de 1,0 segundo)
3. Verificação de Modelo DMA Homologado
4. Limite de Quantidade Máxima por Ordem (Max Order Quantity)
5. Limite de Valor Financeiro Máximo por Ordem (Max Order Value R$)
6. Túnel de Preço Rejeição (% de variação contra preço de referência)
7. Controle de Posição Máxima Descoberta intradiária (Short-Selling Control)

100% Python Standard Library (dataclasses, Decimal, datetime, enum, json).
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
import json
from pathlib import Path
import time
from typing import Any, Deque, Dict, List, Optional, Tuple, Union


# ==============================================================================
# 1. Enumerações Canônicas
# ==============================================================================

class DmaModelType(str, Enum):
    """Modelos de Acesso Direto ao Mercado (DMA) regulamentados pela B3."""

    DMA_1 = "DMA_1"  # Tradicional: Conexão via infraestrutura e OMS da Corretora
    DMA_2 = "DMA_2"  # Via Algoritmo: Algoritmos hospedados na Corretora
    DMA_3 = "DMA_3"  # Direct to Exchange: Conexão direta com Gateway de Risco da Corretora
    DMA_4 = "DMA_4"  # Co-location: Servidores no Data Center B3 (Santana de Parnaíba)


class DmaRejectionReason(str, Enum):
    """Códigos padronizados de decisão e motivo de rejeição pré-trade B3 / CVM 35."""

    APPROVED = "APPROVED"
    REJECTED_KILL_SWITCH_ACTIVE = "REJECTED_KILL_SWITCH_ACTIVE"
    REJECTED_THROTTLING_LIMIT_EXCEEDED = "REJECTED_THROTTLING_LIMIT_EXCEEDED"
    REJECTED_DMA_MODEL_MISMATCH = "REJECTED_DMA_MODEL_MISMATCH"
    REJECTED_CLIENT_NOT_FOUND = "REJECTED_CLIENT_NOT_FOUND"
    REJECTED_CLIENT_INACTIVE = "REJECTED_CLIENT_INACTIVE"
    REJECTED_MAX_ORDER_VALUE_EXCEEDED = "REJECTED_MAX_ORDER_VALUE_EXCEEDED"
    REJECTED_MAX_ORDER_QUANTITY_EXCEEDED = "REJECTED_MAX_ORDER_QUANTITY_EXCEEDED"
    REJECTED_PRICE_TUNNEL_EXCEEDED = "REJECTED_PRICE_TUNNEL_EXCEEDED"
    REJECTED_UNCOVERED_POSITION_EXCEEDED = "REJECTED_UNCOVERED_POSITION_EXCEEDED"
    REJECTED_INVALID_ORDER_PARAMETERS = "REJECTED_INVALID_ORDER_PARAMETERS"
    REJECTED_REFERENCE_PRICE_UNAVAILABLE = "REJECTED_REFERENCE_PRICE_UNAVAILABLE"


class OrderSide(str, Enum):
    """Lado da oferta no livro B3."""

    BUY = "BUY"
    SELL = "SELL"


# ==============================================================================
# 2. Modelos de Configuração e Estado
# ==============================================================================

@dataclass
class DmaClientConfig:
    """Configuração de governança e limites pré-trade de um cliente DMA.

    Campos:
        client_id: Identificador único do cliente no gateway.
        account: Código de conta de custódia na B3.
        participant_code: Código do participante de negociação (Corretora PNP).
        dma_model: Modelo de DMA homologado para o cliente (DMA 1, 2, 3 ou 4).
        max_order_value: Valor financeiro máximo permitido por ordem (R$).
        max_order_qty: Quantidade máxima de títulos/contratos permitida por ordem.
        price_tunnel_pct: Tolerância percentual máxima contra o preço de referência.
        max_uncovered_position: Quantidade máxima de venda a descoberto autorizada.
        max_msg_per_second: Taxa máxima de mensagens por segundo (throttling).
        is_active: Status operacional do cadastro do cliente.
        is_kill_switch_active: Se True, todas as ordens são bloqueadas sumariamente.
        custody: Dicionário de posições em custódia própria por ativo (symbol -> qty).
    """

    client_id: str
    account: str
    participant_code: int
    dma_model: DmaModelType
    max_order_value: Decimal
    max_order_qty: int
    price_tunnel_pct: Decimal
    max_uncovered_position: int
    max_msg_per_second: int
    is_active: bool = True
    is_kill_switch_active: bool = False
    custody: Dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DmaClientConfig:
        """Cria instância a partir de dicionário deserializado."""
        raw_model = data["dma_model"]
        dma_model = raw_model if isinstance(raw_model, DmaModelType) else DmaModelType(raw_model)

        custody_data = {
            str(k).upper(): int(v)
            for k, v in data.get("custody", {}).items()
        }

        return cls(
            client_id=str(data["client_id"]),
            account=str(data["account"]),
            participant_code=int(data["participant_code"]),
            dma_model=dma_model,
            max_order_value=Decimal(str(data["max_order_value"])),
            max_order_qty=int(data["max_order_qty"]),
            price_tunnel_pct=Decimal(str(data["price_tunnel_pct"])),
            max_uncovered_position=int(data["max_uncovered_position"]),
            max_msg_per_second=int(data["max_msg_per_second"]),
            is_active=bool(data.get("is_active", True)),
            is_kill_switch_active=bool(data.get("is_kill_switch_active", False)),
            custody=custody_data,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serializa configuração para dicionário com valores primitivos."""
        return {
            "client_id": self.client_id,
            "account": self.account,
            "participant_code": self.participant_code,
            "dma_model": self.dma_model.value,
            "max_order_value": str(self.max_order_value),
            "max_order_qty": self.max_order_qty,
            "price_tunnel_pct": str(self.price_tunnel_pct),
            "max_uncovered_position": self.max_uncovered_position,
            "max_msg_per_second": self.max_msg_per_second,
            "is_active": self.is_active,
            "is_kill_switch_active": self.is_kill_switch_active,
            "custody": dict(self.custody),
        }


@dataclass(frozen=True)
class DmaOrderRequest:
    """Requisição de inserção de ordem submetida via canal DMA.

    Campos:
        order_id: Identificador único da ordem (ClOrdID no protocolo FIX).
        client_id: Código do cliente solicitante.
        symbol: Código do ativo negociado na B3 (ex: PETR4, VALE3).
        side: Lado da operação (BUY ou SELL).
        quantity: Quantidade de títulos/contratos.
        price: Preço unitário da oferta em Decimal.
        timestamp: Carimbo de tempo da ordem (alta precisão).
        dma_model: Modelo de DMA declarado na sessão de origem (opcional).
        account: Conta de custódia declarada (opcional).
    """

    order_id: str
    client_id: str
    symbol: str
    side: Union[OrderSide, str]
    quantity: int
    price: Decimal
    timestamp: datetime
    dma_model: Optional[DmaModelType] = None
    account: Optional[str] = None

    @property
    def normalized_side(self) -> OrderSide:
        """Retorna o lado normalizado como enum OrderSide."""
        if isinstance(self.side, OrderSide):
            return self.side
        val = str(self.side).strip().upper()
        if val in ("1", "BUY", "COMPRA"):
            return OrderSide.BUY
        if val in ("2", "SELL", "VENDA"):
            return OrderSide.SELL
        raise ValueError(f"Lado da ordem inválido: {self.side}")

    @property
    def order_value(self) -> Decimal:
        """Calcula o valor financeiro bruto da ordem (Quantidade x Preço)."""
        return Decimal(self.quantity) * self.price

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DmaOrderRequest:
        """Cria requisição a partir de dicionário."""
        ts = data.get("timestamp")
        if isinstance(ts, datetime):
            parsed_ts = ts
        elif isinstance(ts, str):
            clean_ts = ts.replace("Z", "+00:00")
            parsed_ts = datetime.fromisoformat(clean_ts)
        else:
            parsed_ts = datetime.now(timezone.utc)

        raw_model = data.get("dma_model")
        dma_model = None
        if raw_model:
            dma_model = raw_model if isinstance(raw_model, DmaModelType) else DmaModelType(raw_model)

        return cls(
            order_id=str(data["order_id"]),
            client_id=str(data["client_id"]),
            symbol=str(data["symbol"]).upper().strip(),
            side=str(data["side"]).upper().strip(),
            quantity=int(data["quantity"]),
            price=Decimal(str(data["price"])),
            timestamp=parsed_ts,
            dma_model=dma_model,
            account=str(data["account"]) if "account" in data and data["account"] is not None else None,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serializa ordem para dicionário."""
        return {
            "order_id": self.order_id,
            "client_id": self.client_id,
            "symbol": self.symbol,
            "side": self.normalized_side.value,
            "quantity": self.quantity,
            "price": str(self.price),
            "timestamp": self.timestamp.isoformat(),
            "dma_model": self.dma_model.value if self.dma_model else None,
            "account": self.account,
            "order_value": str(self.order_value),
        }


@dataclass(frozen=True)
class DmaRiskEvaluationResult:
    """Resultado da avaliação pré-trade pelo gateway de governança DMA.

    Campos:
        order_id: Identificador da ordem avaliada.
        client_id: Código do cliente.
        approved: True se a ordem foi aprovada para seguir ao PUMA; False caso rejeitada.
        reason_code: Código da decisão (APPROVED ou motivo de rejeição).
        message: Descrição explicativa da decisão regulatória.
        evaluation_latency_us: Latência da avaliação pré-trade em microssegundos (µs).
        timestamp: Carimbo de tempo em que a avaliação foi concluída.
        dma_model: Modelo de DMA sob o qual a ordem foi avaliada.
        details: Detalhes quantitativos adicionais (preço de referência, desvio percentual, etc).
    """

    order_id: str
    client_id: str
    approved: bool
    reason_code: DmaRejectionReason
    message: str
    evaluation_latency_us: float
    timestamp: datetime
    dma_model: DmaModelType
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o resultado para formato legível em JSON."""
        return {
            "order_id": self.order_id,
            "client_id": self.client_id,
            "approved": self.approved,
            "reason_code": self.reason_code.value,
            "message": self.message,
            "evaluation_latency_us": round(self.evaluation_latency_us, 3),
            "timestamp": self.timestamp.isoformat(),
            "dma_model": self.dma_model.value,
            "details": self.details,
        }


@dataclass(frozen=True)
class KillSwitchExecution:
    """Registro de disparo emergencial de Kill-Switch mandatário.

    Campos:
        client_id: Cliente afetado pelo desarme.
        trigger_timestamp: Momento em que o Kill-Switch foi acionado.
        executed_by: Responsável pelo acionamento (sistema ou operador de compliance).
        reason: Justificativa operacional do disparo.
        canceled_orders_count: Quantidade de ordens ativas canceladas em massa.
        canceled_order_ids: Lista dos identificadores das ordens canceladas.
        channel_blocked: True confirmando o bloqueio do canal de novas ordens.
        latency_ms: Latência de execução do cancelamento massivo em milissegundos (< 100ms).
    """

    client_id: str
    trigger_timestamp: datetime
    executed_by: str
    reason: str
    canceled_orders_count: int
    canceled_order_ids: List[str]
    channel_blocked: bool
    latency_ms: float

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o registro de execução de Kill-Switch."""
        return {
            "client_id": self.client_id,
            "trigger_timestamp": self.trigger_timestamp.isoformat(),
            "executed_by": self.executed_by,
            "reason": self.reason,
            "canceled_orders_count": self.canceled_orders_count,
            "canceled_order_ids": list(self.canceled_order_ids),
            "channel_blocked": self.channel_blocked,
            "latency_ms": round(self.latency_ms, 3),
        }


# ==============================================================================
# 3. Motor Central de Governança DMA e Risco Pré-Trade
# ==============================================================================

class DmaGovernanceEngine:
    """Motor de Governança DMA e Gateway de Risco Pré-Trade B3 (FL-045).

    Executa em sub-milissegundo todas as validações mandatórias da Resolução CVM 35,
    controla throttling por janela deslizante de 1,0s e provê o mecanismo de Kill-Switch
    com cancelamento em massa em menos de 100 milissegundos.
    """

    def __init__(self) -> None:
        """Inicializa estruturas de dados em memória de alta performance."""
        # Configurações de clientes registradas (client_id -> DmaClientConfig)
        self._clients: Dict[str, DmaClientConfig] = {}

        # Preços de referência de mercado por ativo (symbol -> Decimal)
        self._reference_prices: Dict[str, Decimal] = {}

        # Ordens ativas no livro / pendentes de execução (client_id -> dict of order_id -> DmaOrderRequest)
        self._active_orders: Dict[str, Dict[str, DmaOrderRequest]] = {}

        # Janela deslizante de carimbos de tempo para throttling (client_id -> Deque de timestamps POSIX em float)
        self._sliding_windows: Dict[str, Deque[float]] = {}

        # Trilha de auditoria de disparos de Kill-Switch
        self._kill_switch_history: List[KillSwitchExecution] = []

    # --------------------------------------------------------------------------
    # Gestão de Clientes e Parâmetros
    # --------------------------------------------------------------------------

    def register_client(self, config: DmaClientConfig) -> None:
        """Registra ou atualiza as configurações de governança de um cliente DMA."""
        self._clients[config.client_id] = config
        if config.client_id not in self._active_orders:
            self._active_orders[config.client_id] = {}
        if config.client_id not in self._sliding_windows:
            self._sliding_windows[config.client_id] = deque()

    def get_client(self, client_id: str) -> Optional[DmaClientConfig]:
        """Obtém configuração do cliente pelo identificador."""
        return self._clients.get(client_id)

    def set_reference_price(self, symbol: str, price: Union[Decimal, float, str, int]) -> None:
        """Define o preço de referência de mercado para validação do túnel de preço."""
        sym = symbol.strip().upper()
        self._reference_prices[sym] = Decimal(str(price))

    def get_reference_price(self, symbol: str) -> Optional[Decimal]:
        """Obtém o preço de referência atual de um ativo."""
        return self._reference_prices.get(symbol.strip().upper())

    def set_client_custody(self, client_id: str, symbol: str, quantity: int) -> None:
        """Define a posição de custódia de um cliente para determinado ativo."""
        client = self._clients.get(client_id)
        if not client:
            raise KeyError(f"Cliente {client_id} não encontrado.")
        client.custody[symbol.strip().upper()] = int(quantity)

    def get_client_custody(self, client_id: str, symbol: str) -> int:
        """Obtém a custódia disponível de um cliente para o ativo informado."""
        client = self._clients.get(client_id)
        if not client:
            return 0
        return client.custody.get(symbol.strip().upper(), 0)

    def get_active_orders(self, client_id: str) -> List[DmaOrderRequest]:
        """Retorna lista de ordens atualmente ativas/abertas para o cliente."""
        orders_dict = self._active_orders.get(client_id, {})
        return list(orders_dict.values())

    def get_kill_switch_history(self) -> List[KillSwitchExecution]:
        """Retorna o histórico cronológico de acionamentos de Kill-Switch."""
        return list(self._kill_switch_history)

    # --------------------------------------------------------------------------
    # Avaliação de Ofertas (Pipeline Pré-Trade Determinístico)
    # --------------------------------------------------------------------------

    def evaluate_order(self, request: DmaOrderRequest) -> DmaRiskEvaluationResult:
        """Avalia deterministicamente uma requisição de ordem contra todos os filtros pré-trade.

        Pipeline de Avaliação:
        1. Verificação de cadastro e ativação do cliente
        2. Trava de Kill-Switch (bloqueio do canal)
        3. Throttling de mensagens (janela deslizante de 1,0s)
        4. Sanidade de parâmetros (quantidade > 0, preço > 0, lado válido)
        5. Validação de conformidade do Modelo de DMA
        6. Limite de Quantidade Máxima por Ordem (Max Order Quantity)
        7. Limite de Valor Financeiro Máximo por Ordem (Max Order Value)
        8. Túnel de Preço Rejeição (% de variação contra referência)
        9. Controle de Posição Máxima Descoberta intradiária (Short Selling)
        """
        start_ns = time.perf_counter_ns()
        now_dt = datetime.now(timezone.utc)

        # 1. Cliente cadastrado
        client = self._clients.get(request.client_id)
        if not client:
            latency_us = (time.perf_counter_ns() - start_ns) / 1000.0
            return DmaRiskEvaluationResult(
                order_id=request.order_id,
                client_id=request.client_id,
                approved=False,
                reason_code=DmaRejectionReason.REJECTED_CLIENT_NOT_FOUND,
                message=f"Cliente {request.client_id} não cadastrado na governança DMA.",
                evaluation_latency_us=latency_us,
                timestamp=now_dt,
                dma_model=request.dma_model or DmaModelType.DMA_1,
            )

        # 2. Cliente ativo
        if not client.is_active:
            latency_us = (time.perf_counter_ns() - start_ns) / 1000.0
            return DmaRiskEvaluationResult(
                order_id=request.order_id,
                client_id=request.client_id,
                approved=False,
                reason_code=DmaRejectionReason.REJECTED_CLIENT_INACTIVE,
                message=f"Cadastro do cliente {request.client_id} inativo no intermediário.",
                evaluation_latency_us=latency_us,
                timestamp=now_dt,
                dma_model=client.dma_model,
            )

        # 3. Kill-Switch ativo (Canal Bloqueado)
        if client.is_kill_switch_active:
            latency_us = (time.perf_counter_ns() - start_ns) / 1000.0
            return DmaRiskEvaluationResult(
                order_id=request.order_id,
                client_id=request.client_id,
                approved=False,
                reason_code=DmaRejectionReason.REJECTED_KILL_SWITCH_ACTIVE,
                message=f"Canal DMA bloqueado por acionamento emergencial de Kill-Switch para o cliente {request.client_id}.",
                evaluation_latency_us=latency_us,
                timestamp=now_dt,
                dma_model=client.dma_model,
                details={"kill_switch_active": True},
            )

        # 4. Throttling de Mensagens (Janela Deslizante de 1,0s)
        now_epoch = request.timestamp.timestamp() if request.timestamp else time.time()
        window = self._sliding_windows.setdefault(client.client_id, deque())
        
        # Remove timestamps com mais de 1,0 segundo
        threshold = now_epoch - 1.0
        while window and window[0] <= threshold:
            window.popleft()

        if len(window) >= client.max_msg_per_second:
            latency_us = (time.perf_counter_ns() - start_ns) / 1000.0
            return DmaRiskEvaluationResult(
                order_id=request.order_id,
                client_id=request.client_id,
                approved=False,
                reason_code=DmaRejectionReason.REJECTED_THROTTLING_LIMIT_EXCEEDED,
                message=(
                    f"Taxa de mensagens excedida ({len(window)} >= {client.max_msg_per_second} msg/s) "
                    f"na janela de 1 segundo para o cliente {request.client_id}."
                ),
                evaluation_latency_us=latency_us,
                timestamp=now_dt,
                dma_model=client.dma_model,
                details={
                    "current_rate_mps": len(window),
                    "max_msg_per_second": client.max_msg_per_second,
                },
            )

        # Registra a tentativa de envio na janela deslizante
        window.append(now_epoch)

        # 5. Sanidade dos parâmetros da ordem
        try:
            side = request.normalized_side
        except ValueError as err:
            latency_us = (time.perf_counter_ns() - start_ns) / 1000.0
            return DmaRiskEvaluationResult(
                order_id=request.order_id,
                client_id=request.client_id,
                approved=False,
                reason_code=DmaRejectionReason.REJECTED_INVALID_ORDER_PARAMETERS,
                message=str(err),
                evaluation_latency_us=latency_us,
                timestamp=now_dt,
                dma_model=client.dma_model,
            )

        if request.quantity <= 0 or request.price <= Decimal("0"):
            latency_us = (time.perf_counter_ns() - start_ns) / 1000.0
            return DmaRiskEvaluationResult(
                order_id=request.order_id,
                client_id=request.client_id,
                approved=False,
                reason_code=DmaRejectionReason.REJECTED_INVALID_ORDER_PARAMETERS,
                message="Quantidade e preço devem ser estritamente positivos.",
                evaluation_latency_us=latency_us,
                timestamp=now_dt,
                dma_model=client.dma_model,
                details={"quantity": request.quantity, "price": str(request.price)},
            )

        # 6. Modelo de DMA Homologado
        if request.dma_model is not None and request.dma_model != client.dma_model:
            latency_us = (time.perf_counter_ns() - start_ns) / 1000.0
            return DmaRiskEvaluationResult(
                order_id=request.order_id,
                client_id=request.client_id,
                approved=False,
                reason_code=DmaRejectionReason.REJECTED_DMA_MODEL_MISMATCH,
                message=(
                    f"Ordem declara modelo {request.dma_model.value}, mas canal do cliente "
                    f"está homologado exclusivamente para {client.dma_model.value}."
                ),
                evaluation_latency_us=latency_us,
                timestamp=now_dt,
                dma_model=client.dma_model,
                details={
                    "declared_dma_model": request.dma_model.value,
                    "authorized_dma_model": client.dma_model.value,
                },
            )

        # 7. Limite de Quantidade Máxima por Ordem
        if request.quantity > client.max_order_qty:
            latency_us = (time.perf_counter_ns() - start_ns) / 1000.0
            return DmaRiskEvaluationResult(
                order_id=request.order_id,
                client_id=request.client_id,
                approved=False,
                reason_code=DmaRejectionReason.REJECTED_MAX_ORDER_QUANTITY_EXCEEDED,
                message=(
                    f"Quantidade da ordem ({request.quantity:,}) excede o teto contratual "
                    f"de {client.max_order_qty:,} títulos."
                ),
                evaluation_latency_us=latency_us,
                timestamp=now_dt,
                dma_model=client.dma_model,
                details={"requested_qty": request.quantity, "max_order_qty": client.max_order_qty},
            )

        # 8. Limite de Valor Financeiro Máximo por Ordem (Fat-Finger)
        order_val = request.order_value
        if order_val > client.max_order_value:
            latency_us = (time.perf_counter_ns() - start_ns) / 1000.0
            return DmaRiskEvaluationResult(
                order_id=request.order_id,
                client_id=request.client_id,
                approved=False,
                reason_code=DmaRejectionReason.REJECTED_MAX_ORDER_VALUE_EXCEEDED,
                message=(
                    f"Valor financeiro da ordem (R$ {order_val:,.2f}) excede o limite "
                    f"máximo parametrizado de R$ {client.max_order_value:,.2f}."
                ),
                evaluation_latency_us=latency_us,
                timestamp=now_dt,
                dma_model=client.dma_model,
                details={"order_value": str(order_val), "max_order_value": str(client.max_order_value)},
            )

        # 9. Túnel de Preço Rejeição (% de variação contra preço de referência)
        sym = request.symbol.strip().upper()
        ref_price = self._reference_prices.get(sym)
        if ref_price is None:
            latency_us = (time.perf_counter_ns() - start_ns) / 1000.0
            return DmaRiskEvaluationResult(
                order_id=request.order_id,
                client_id=request.client_id,
                approved=False,
                reason_code=DmaRejectionReason.REJECTED_REFERENCE_PRICE_UNAVAILABLE,
                message=f"Preço de referência indisponível para o ativo {sym} na B3.",
                evaluation_latency_us=latency_us,
                timestamp=now_dt,
                dma_model=client.dma_model,
            )

        price_diff = abs(request.price - ref_price)
        deviation_pct = price_diff / ref_price

        if deviation_pct > client.price_tunnel_pct:
            latency_us = (time.perf_counter_ns() - start_ns) / 1000.0
            return DmaRiskEvaluationResult(
                order_id=request.order_id,
                client_id=request.client_id,
                approved=False,
                reason_code=DmaRejectionReason.REJECTED_PRICE_TUNNEL_EXCEEDED,
                message=(
                    f"Preço da ordem (R$ {request.price}) diverge {deviation_pct * 100:.2f}% "
                    f"do preço de referência (R$ {ref_price}), superando o túnel de "
                    f"rejeição de {client.price_tunnel_pct * 100:.2f}%."
                ),
                evaluation_latency_us=latency_us,
                timestamp=now_dt,
                dma_model=client.dma_model,
                details={
                    "price": str(request.price),
                    "reference_price": str(ref_price),
                    "deviation_pct": str(round(deviation_pct, 6)),
                    "tunnel_limit_pct": str(client.price_tunnel_pct),
                },
            )

        # 10. Controle de Posição Máxima Descoberta Intradiária (Short Selling)
        if side == OrderSide.SELL:
            custody = client.custody.get(sym, 0)
            
            # Somatório de ordens de venda ativas do cliente para este ativo
            client_active = self._active_orders.get(client.client_id, {})
            pending_sell_qty = sum(
                active_req.quantity
                for active_req in client_active.values()
                if active_req.symbol.upper() == sym and active_req.normalized_side == OrderSide.SELL
            )

            total_projected_sell = pending_sell_qty + request.quantity
            uncovered_qty = max(0, total_projected_sell - custody)

            if uncovered_qty > client.max_uncovered_position:
                latency_us = (time.perf_counter_ns() - start_ns) / 1000.0
                return DmaRiskEvaluationResult(
                    order_id=request.order_id,
                    client_id=request.client_id,
                    approved=False,
                    reason_code=DmaRejectionReason.REJECTED_UNCOVERED_POSITION_EXCEEDED,
                    message=(
                        f"Venda a descoberto projetada ({uncovered_qty:,} ações) excede o limite "
                        f"máximo intradiário de {client.max_uncovered_position:,} ações para o ativo {sym}."
                    ),
                    evaluation_latency_us=latency_us,
                    timestamp=now_dt,
                    dma_model=client.dma_model,
                    details={
                        "custody": custody,
                        "pending_sell_qty": pending_sell_qty,
                        "requested_qty": request.quantity,
                        "projected_uncovered": uncovered_qty,
                        "max_uncovered_position": client.max_uncovered_position,
                    },
                )

        # Ordem Aprovada com Sucesso em todos os 10 filtros!
        self._active_orders[client.client_id][request.order_id] = request
        latency_us = (time.perf_counter_ns() - start_ns) / 1000.0

        return DmaRiskEvaluationResult(
            order_id=request.order_id,
            client_id=request.client_id,
            approved=True,
            reason_code=DmaRejectionReason.APPROVED,
            message="Ordem aprovada nos filtros pré-trade de governança DMA B3.",
            evaluation_latency_us=latency_us,
            timestamp=now_dt,
            dma_model=client.dma_model,
            details={
                "order_value": str(order_val),
                "quantity": request.quantity,
                "price_deviation_pct": str(round(deviation_pct, 6)),
            },
        )

    # --------------------------------------------------------------------------
    # Ciclo de Vida de Ordens (Execuções e Cancelamentos)
    # --------------------------------------------------------------------------

    def cancel_order(self, client_id: str, order_id: str) -> bool:
        """Remove ordem do livro de ordens ativas do cliente."""
        client_orders = self._active_orders.get(client_id)
        if client_orders and order_id in client_orders:
            del client_orders[order_id]
            return True
        return False

    def register_fill(
        self,
        client_id: str,
        order_id: str,
        filled_qty: int,
        price: Optional[Decimal] = None,
    ) -> bool:
        """Registra execução parcial ou total de uma ordem ativa."""
        client_orders = self._active_orders.get(client_id)
        if not client_orders or order_id not in client_orders:
            return False

        order = client_orders[order_id]
        client = self._clients.get(client_id)

        # Atualiza custódia em caso de execução de venda ou compra
        if client:
            sym = order.symbol.upper()
            curr_custody = client.custody.get(sym, 0)
            if order.normalized_side == OrderSide.BUY:
                client.custody[sym] = curr_custody + filled_qty
            else:
                client.custody[sym] = curr_custody - filled_qty

        # Se executou integralmente ou mais, remove das ordens ativas
        if filled_qty >= order.quantity:
            del client_orders[order_id]
        else:
            # Atualiza quantidade residual
            remaining = order.quantity - filled_qty
            updated_order = DmaOrderRequest(
                order_id=order.order_id,
                client_id=order.client_id,
                symbol=order.symbol,
                side=order.side,
                quantity=remaining,
                price=order.price,
                timestamp=order.timestamp,
                dma_model=order.dma_model,
                account=order.account,
            )
            client_orders[order_id] = updated_order

        return True

    # --------------------------------------------------------------------------
    # Mecanismo Mandatório de Kill-Switch (< 100 ms)
    # --------------------------------------------------------------------------

    def trigger_kill_switch(
        self,
        client_id: str,
        reason: str,
        executed_by: str = "BROKER_RISK_GATEWAY",
    ) -> KillSwitchExecution:
        """Desarma instantaneamente o canal DMA e executa cancelamento massivo das ordens abertas.

        Garantia de latência: Operação em memória concluída em menos de 100 milissegundos.
        """
        start_ns = time.perf_counter_ns()
        now_dt = datetime.now(timezone.utc)

        client = self._clients.get(client_id)
        if not client:
            raise KeyError(f"Cliente {client_id} não encontrado para acionamento de Kill-Switch.")

        # 1. Bloqueia canal DMA
        client.is_kill_switch_active = True

        # 2. Cancelamento em massa das ordens ativas
        client_orders = self._active_orders.get(client_id, {})
        canceled_ids = list(client_orders.keys())
        client_orders.clear()

        # 3. Cálculo de latência da operação em milissegundos
        latency_ms = (time.perf_counter_ns() - start_ns) / 1_000_000.0

        execution = KillSwitchExecution(
            client_id=client_id,
            trigger_timestamp=now_dt,
            executed_by=executed_by,
            reason=reason,
            canceled_orders_count=len(canceled_ids),
            canceled_order_ids=canceled_ids,
            channel_blocked=True,
            latency_ms=latency_ms,
        )

        self._kill_switch_history.append(execution)
        return execution

    def reset_kill_switch(
        self,
        client_id: str,
        reset_by: str = "COMPLIANCE_OFFICER",
    ) -> bool:
        """Restabelece o canal DMA do cliente após liberação do Compliance / Risco."""
        client = self._clients.get(client_id)
        if not client:
            return False

        client.is_kill_switch_active = False
        return True

    # --------------------------------------------------------------------------
    # Carga de Fixtures e Serialização
    # --------------------------------------------------------------------------

    def load_from_dict(self, data: Dict[str, Any]) -> None:
        """Carrega preços de referência e perfis de clientes a partir de dicionário."""
        # Preços de referência
        ref_prices = data.get("reference_prices", {})
        for sym, price in ref_prices.items():
            self.set_reference_price(sym, price)

        # Clientes
        clients_list = data.get("clients", [])
        for client_raw in clients_list:
            config = DmaClientConfig.from_dict(client_raw)
            self.register_client(config)

    def load_from_json_file(self, file_path: Union[str, Path]) -> None:
        """Carrega configuração completa a partir de arquivo JSON."""
        path = Path(file_path)
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        self.load_from_dict(data)
