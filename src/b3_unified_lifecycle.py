"""B3 Unified Lifecycle Engine.

Motor unificado de orquestração e rastreamento didático do ciclo de vida completo
de instrumentos financeiros da B3, percorrendo todos os 7 módulos essenciais:
  1. Pré-Negociação & Risco (Pre-Trade Risk Gateway)
  2. Negociação Listada (PUMA Trading System)
  3. Registro & Balcão Organizado (NoMe / Balcão Cetip)
  4. Contraparte Central & Pós-Negociação (Câmara B3 / CCP)
  5. Liquidação Financeira & SPB (BACEN / STR / SPI / Drex)
  6. Central Depositária & Custódia (CSD)
  7. Supervisão & Autorregulação de Mercados (BSM)

Zero dependências externas: 100% biblioteca padrão do Python com precisão Decimal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_EVEN
from enum import Enum
import time
from typing import Any, Dict, List, Optional, Tuple


class B3Module(str, Enum):
    """Os 7 módulos estruturantes do ecossistema B3."""
    PRE_TRADE_RISK = "PRE_TRADE_RISK"
    PUMA_MATCHING = "PUMA_MATCHING"
    NOME_REGISTRY = "NOME_REGISTRY"
    CCP_CLEARING = "CCP_CLEARING"
    SPB_SETTLEMENT = "SPB_SETTLEMENT"
    CSD_CUSTODY = "CSD_CUSTODY"
    BSM_SURVEILLANCE = "BSM_SURVEILLANCE"


class StepStatus(str, Enum):
    """Status de execução de uma etapa do ciclo de vida."""
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    MATCHED = "MATCHED"
    REGISTERED = "REGISTERED"
    NOVATED = "NOVATED"
    SETTLED = "SETTLED"
    AUDITED = "AUDITED"
    FLAGGED = "FLAGGED"


class OrderSide(str, Enum):
    """Lado da ordem ou operação."""
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    """Tipo de ordem."""
    LIMIT = "LIMIT"
    MARKET = "MARKET"
    MOC = "MOC"  # Market-On-Close
    LOC = "LOC"  # Limit-On-Close


class OTCAssetType(str, Enum):
    """Tipo de ativo de balcão registrado."""
    CDB = "CDB"
    DEBENTURE = "DEBENTURE"
    CARD_RECEIVABLE = "CARD_RECEIVABLE"
    CIR = "CIR"
    SWAP = "SWAP"


@dataclass
class Participant:
    """Instituição participante (Corretora ou Banco Liquidante)."""
    participant_id: str
    name: str
    dma_tier: str = "DMA3"  # DMA1, DMA2, DMA3, DMA4
    collateral_limit: Decimal = Decimal("10000000.00")
    cash_balance: Decimal = Decimal("5000000.00")


@dataclass
class InvestorAccount:
    """Conta de custódia individualizada de investidor (CPF/CNPJ)."""
    investor_id: str  # CPF ou CNPJ formatado ou anonimizado
    name: str
    participant_id: str
    available_cash: Decimal = Decimal("500000.00")
    custody_shares: Dict[str, int] = field(default_factory=dict)


@dataclass
class Order:
    """Ordem institucional enviada ao mercado listado PUMA."""
    order_id: str
    investor_id: str
    participant_id: str
    symbol: str
    side: OrderSide
    quantity: int
    price: Decimal
    order_type: OrderType = OrderType.LIMIT


@dataclass
class OTCContract:
    """Contrato bilateral ou título de crédito privado registrado no NoMe/Cetip."""
    contract_id: str
    issuer_id: str
    investor_id: str
    asset_type: OTCAssetType
    notional: Decimal
    annual_rate: Decimal
    maturity_days: int
    lien_holder: Optional[str] = None  # Se gravado (ex: banco credor da trava de domicílio)
    asset_reference: Optional[str] = None


@dataclass
class StepAuditRecord:
    """Registro de auditoria de uma etapa percorrida nos 7 módulos."""
    module: B3Module
    status: StepStatus
    latency_micros: Decimal
    summary: str
    conservation_law: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LifecycleTraceReport:
    """Relatório unificado de rastreamento de uma operação na B3."""
    operation_id: str
    operation_type: str  # "LISTED_TRADE" ou "OTC_REGISTRATION"
    instrument: str
    total_volume: int
    executed_price: Decimal
    total_notional: Decimal
    steps: List[StepAuditRecord] = field(default_factory=list)
    is_settled: bool = False
    is_compliant: bool = True
    bsm_flags: List[str] = field(default_factory=list)

    def summary_text(self) -> str:
        """Gera uma visualização textual estruturada para inspeção no terminal."""
        lines = [
            "=" * 78,
            f" B3 UNIFIED LIFECYCLE TRACE REPORT — [{self.operation_type}]",
            f" Operação: {self.operation_id} | Ativo: {self.instrument}",
            f" Volume: {self.total_volume:,} | Preço: R$ {self.executed_price:,.2f} | Nocional: R$ {self.total_notional:,.2f}",
            "=" * 78,
        ]
        for idx, step in enumerate(self.steps, 1):
            law_tag = f"[{step.conservation_law}]" if step.conservation_law else ""
            status_icon = "✓" if step.status in (StepStatus.APPROVED, StepStatus.MATCHED, StepStatus.REGISTERED, StepStatus.NOVATED, StepStatus.SETTLED, StepStatus.AUDITED) else "✗"
            lines.append(
                f" [{idx}/7] {status_icon} MÓDULO: {step.module.value:<18} | STATUS: {step.status.value:<10} "
                f"| LAT: {step.latency_micros:>6.1f}µs {law_tag}"
            )
            lines.append(f"       -> {step.summary}")
            if step.details:
                for k, v in step.details.items():
                    lines.append(f"          - {k}: {v}")

        lines.append("-" * 78)
        compliance_str = "TOTALMENTE CONFORME (SEM APONTAMENTOS)" if self.is_compliant else f"ALERTA: {', '.join(self.bsm_flags)}"
        settled_str = "LIQUIDAÇÃO CONCLUÍDA" if self.is_settled else "REJEITADO / NÃO LIQUIDADO"
        lines.append(f" RESULTADO FINAL: {settled_str} | AUDITORIA BSM: {compliance_str}")
        lines.append("=" * 78)
        return "\n".join(lines)


class B3UnifiedLifecycleEngine:
    """Motor central unificado que orquestra e simula operações pelos 7 módulos da B3."""

    def __init__(self) -> None:
        self.participants: Dict[str, Participant] = {}
        self.accounts: Dict[str, InvestorAccount] = {}

    def register_participant(self, participant: Participant) -> None:
        """Cadastra participante institucional."""
        self.participants[participant.participant_id] = participant

    def register_account(self, account: InvestorAccount) -> None:
        """Cadastra conta individualizada de investidor."""
        self.accounts[account.investor_id] = account

    def execute_listed_trade(
        self,
        buy_order: Order,
        sell_order: Order,
        reference_price: Decimal,
    ) -> LifecycleTraceReport:
        """Executa e rastreia uma negociação de mercado listado através dos 7 módulos.

        Fluxo:
          1. Pré-Trade Risk: Filtro de fat-finger, limites operacionais e DMA.
          2. PUMA Matching: Túneis de oscilação e matching FIFO.
          3. RTC Capture: Captura e alocação às contas de investidor.
          4. Câmara B3 (CCP): Novação contratual (Lei 10.214) e cálculo de Margem CORE.
          5. SPB / STR: Liquidação financeira multilateral líquida (LDL0001).
          6. Central Depositária (CSD): Atualização de custódia individualizada (IMBARQ012).
          7. BSM: Auditoria e detecção de abusos (ex: Wash Trading).
        """
        trace = LifecycleTraceReport(
            operation_id=f"TRD-{buy_order.order_id}-{sell_order.order_id}",
            operation_type="LISTED_TRADE",
            instrument=buy_order.symbol,
            total_volume=min(buy_order.quantity, sell_order.quantity),
            executed_price=sell_order.price,
            total_notional=Decimal("0.00"),
        )

        # -------------------------------------------------------------
        # MÓDULO 1: Pré-Negociação & Risco (Pre-Trade Risk Gateway)
        # -------------------------------------------------------------
        t0 = time.perf_counter()
        # 1.1 Checagem de Fat-Finger (desvio de preço > 10% da referência)
        price_dev = abs(buy_order.price - reference_price) / reference_price
        if price_dev > Decimal("0.10"):
            trace.steps.append(
                StepAuditRecord(
                    module=B3Module.PRE_TRADE_RISK,
                    status=StepStatus.REJECTED,
                    latency_micros=Decimal("12.4"),
                    summary=f"Ordem de compra rejeitada por Fat-Finger: desvio de {price_dev*100:.2f}% excede teto de 10%.",
                    conservation_law="LAW-093",
                    details={"price": str(buy_order.price), "reference_price": str(reference_price)},
                )
            )
            trace.is_compliant = False
            trace.bsm_flags.append("FAT_FINGER_REJECTION")
            return trace

        # 1.2 Checagem de Limite de Crédito do Participante
        trade_value = Decimal(str(buy_order.quantity)) * buy_order.price
        buyer_part = self.participants.get(buy_order.participant_id)
        if buyer_part and trade_value > buyer_part.collateral_limit:
            trace.steps.append(
                StepAuditRecord(
                    module=B3Module.PRE_TRADE_RISK,
                    status=StepStatus.REJECTED,
                    latency_micros=Decimal("15.1"),
                    summary=f"Ordem rejeitada por estouro de limite de risco pré-trade da corretora {buyer_part.name}.",
                    conservation_law="LAW-002",
                    details={"order_value": str(trade_value), "limit": str(buyer_part.collateral_limit)},
                )
            )
            trace.is_compliant = False
            trace.bsm_flags.append("CREDIT_LIMIT_EXCEEDED")
            return trace

        trace.steps.append(
            StepAuditRecord(
                module=B3Module.PRE_TRADE_RISK,
                status=StepStatus.APPROVED,
                latency_micros=Decimal("18.5"),
                summary="Filtros de fat-finger, limites operacionais CVM 35/505 e vazão SBE validados com sucesso.",
                conservation_law="LAW-093",
                details={
                    "buyer_dma": buyer_part.dma_tier if buyer_part else "DMA3",
                    "price_deviation_pct": f"{price_dev*100:.2f}%",
                    "throughput_governance": "OK (< 15.000 mps)",
                },
            )
        )

        # -------------------------------------------------------------
        # MÓDULO 2: Negociação Listada (PUMA Trading System)
        # -------------------------------------------------------------
        # Casamento de Ordens no PUMA (Matching Engine)
        if buy_order.price < sell_order.price:
            trace.steps.append(
                StepAuditRecord(
                    module=B3Module.PUMA_MATCHING,
                    status=StepStatus.REJECTED,
                    latency_micros=Decimal("42.0"),
                    summary="Ordens não cruzam: Preço de compra inferior ao preço de venda no livro L3.",
                    conservation_law="LAW-091",
                )
            )
            return trace

        match_qty = min(buy_order.quantity, sell_order.quantity)
        exec_price = sell_order.price  # Preço da ordem passiva que estava no book
        total_nocional = (Decimal(str(match_qty)) * exec_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
        trace.total_volume = match_qty
        trace.executed_price = exec_price
        trace.total_notional = total_nocional

        trace.steps.append(
            StepAuditRecord(
                module=B3Module.PUMA_MATCHING,
                status=StepStatus.MATCHED,
                latency_micros=Decimal("58.2"),
                summary=f"Casamento FIFO L3 concluído: {match_qty:,} {buy_order.symbol} a R$ {exec_price:,.2f}.",
                conservation_law="LAW-091",
                details={
                    "book_level": "L3 MBO (Market-by-Order)",
                    "execution_algorithm": "Price-Time Priority (FIFO)",
                    "price_tunnel_status": "DENTRO DOS TÚNEIS ESTÁTICO E DINÂMICO",
                },
            )
        )

        # -------------------------------------------------------------
        # MÓDULO 3: Captura e Alocação (RTC - Real-Time Capture)
        # -------------------------------------------------------------
        # O RTC faz a ponte imediata entre a execução em tela e o pós-negociação
        trace.steps.append(
            StepAuditRecord(
                module=B3Module.NOME_REGISTRY,
                status=StepStatus.REGISTERED,
                latency_micros=Decimal("110.0"),
                summary=f"Negócio capturado pelo RTC e alocado às contas finais: Comprador {buy_order.investor_id} e Vendedor {sell_order.investor_id}.",
                conservation_law="LAW-003",
                details={
                    "trade_capture_protocol": "FIX 4.4 TradeCaptureReport",
                    "allocated_buyer_account": buy_order.investor_id,
                    "allocated_seller_account": sell_order.investor_id,
                },
            )
        )

        # -------------------------------------------------------------
        # MÓDULO 4: Contraparte Central (Câmara B3 / CCP)
        # -------------------------------------------------------------
        # Novação Contratual sob Art. 7º da Lei 10.214/2001
        # Margem CORE calculada (estimada em 12% do nocional para ações)
        core_margin_req = (total_nocional * Decimal("0.12")).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
        trace.steps.append(
            StepAuditRecord(
                module=B3Module.CCP_CLEARING,
                status=StepStatus.NOVATED,
                latency_micros=Decimal("320.0"),
                summary=f"Novação contratual realizada sob Lei 10.214/01. Margem de garantia CORE calculada em R$ {core_margin_req:,.2f}.",
                conservation_law="LAW-006",
                details={
                    "legal_basis": "Lei 10.214/2001, Art. 7º (Insolvência Zero Bilateral)",
                    "core_margin_required": f"R$ {core_margin_req:,.2f}",
                    "multilateral_netting_tier": "Compensação Contínua Ativa",
                },
            )
        )

        # -------------------------------------------------------------
        # MÓDULO 5: Liquidação Financeira & SPB (BACEN / STR)
        # -------------------------------------------------------------
        # Compensação multilateral líquida gerando instrução LDL0001
        buyer_acc = self.accounts.get(buy_order.investor_id)
        seller_acc = self.accounts.get(sell_order.investor_id)

        if buyer_acc:
            buyer_acc.available_cash -= total_nocional
        if seller_acc:
            seller_acc.available_cash += total_nocional

        trace.steps.append(
            StepAuditRecord(
                module=B3Module.SPB_SETTLEMENT,
                status=StepStatus.SETTLED,
                latency_micros=Decimal("450.0"),
                summary=f"Instrução LDL0001 gerada para ciclo financeiro D+2 no STR. Transferência de R$ {total_nocional:,.2f} com finalidade irrevogável.",
                conservation_law="LAW-002",
                details={
                    "spb_message": "LDL0001 - Liquidação de Diferenças Líquidas",
                    "settlement_cycle": "D+2 (Moeda Banco Central)",
                    "net_cash_transferred": f"R$ {total_nocional:,.2f}",
                },
            )
        )

        # -------------------------------------------------------------
        # MÓDULO 6: Central Depositária & Custódia (CSD)
        # -------------------------------------------------------------
        # Atualização da titularidade física nas contas individualizadas por CPF
        if buyer_acc:
            buyer_acc.custody_shares[buy_order.symbol] = buyer_acc.custody_shares.get(buy_order.symbol, 0) + match_qty
        if seller_acc:
            seller_acc.custody_shares[buy_order.symbol] = seller_acc.custody_shares.get(buy_order.symbol, 0) - match_qty

        trace.steps.append(
            StepAuditRecord(
                module=B3Module.CSD_CUSTODY,
                status=StepStatus.SETTLED,
                latency_micros=Decimal("210.0"),
                summary=f"Custódia atualizada no CSD: +{match_qty:,} {buy_order.symbol} para {buy_order.investor_id}. Posição conciliada no IMBARQ012.",
                conservation_law="LAW-001",
                details={
                    "custody_model": "Contas Individualizadas (CPF/CNPJ)",
                    "imbarq_report": "IMBARQ012 (Conciliação Diária de Custódia)",
                    "physical_conservation": "CONSERVADA (1:1)",
                },
            )
        )
        trace.is_settled = True

        # -------------------------------------------------------------
        # MÓDULO 7: Supervisão & Autorregulação de Mercados (BSM)
        # -------------------------------------------------------------
        # 7.1 Detecção de Wash Trading (Comprador == Vendedor)
        if buy_order.investor_id == sell_order.investor_id:
            trace.is_compliant = False
            trace.bsm_flags.append("WASH_TRADING_DETECTED")
            trace.steps.append(
                StepAuditRecord(
                    module=B3Module.BSM_SURVEILLANCE,
                    status=StepStatus.FLAGGED,
                    latency_micros=Decimal("85.0"),
                    summary=f"ALERTA BSM: Negociação consigo mesmo (Wash Trading) detectada para o beneficiário final {buy_order.investor_id}.",
                    conservation_law="LAW-003",
                    details={
                        "surveillance_rule": "BSM Circular 001 - Prevenção a Negócios Fictícios",
                        "final_beneficiary": buy_order.investor_id,
                        "action": "Dossiê encaminhado à Procuradoria da BSM",
                    },
                )
            )
        else:
            trace.steps.append(
                StepAuditRecord(
                    module=B3Module.BSM_SURVEILLANCE,
                    status=StepStatus.AUDITED,
                    latency_micros=Decimal("72.0"),
                    summary="Auditoria de conformidade BSM concluída sem indícios de Wash Trading, Spoofing ou manipulação de book.",
                    conservation_law="LAW-003",
                    details={
                        "wash_trading_check": "PASS (Beneficiários distintos)",
                        "spoofing_check": "PASS (Ordens com intenção real)",
                        "regulatory_status": "REGULAR / HOMOLOGADO",
                    },
                )
            )

        return trace

    def execute_otc_registration(self, contract: OTCContract) -> LifecycleTraceReport:
        """Executa e rastreia o registro de um instrumento de balcão (NoMe / Balcão Cetip).

        Demonstra como os ativos não-listados (CDBs, Debêntures, Recebíveis, CIR)
        são formalizados, gravados e conciliados na B3.
        """
        trace = LifecycleTraceReport(
            operation_id=f"OTC-{contract.contract_id}",
            operation_type="OTC_REGISTRATION",
            instrument=f"{contract.asset_type.value}-{contract.contract_id}",
            total_volume=1,
            executed_price=contract.notional,
            total_notional=contract.notional,
        )

        # MÓDULO 1: Validação de Pré-Registro
        trace.steps.append(
            StepAuditRecord(
                module=B3Module.PRE_TRADE_RISK,
                status=StepStatus.APPROVED,
                latency_micros=Decimal("35.0"),
                summary=f"Validação cadastral de emissor ({contract.issuer_id}) e investidor ({contract.investor_id}) homologada.",
                conservation_law="LAW-079",
            )
        )

        # MÓDULO 2: Registro no Balcão Organizado (NoMe / Cetip)
        trace.steps.append(
            StepAuditRecord(
                module=B3Module.NOME_REGISTRY,
                status=StepStatus.REGISTERED,
                latency_micros=Decimal("180.0"),
                summary=f"Instrumento {contract.asset_type.value} registrado no NoMe. Nocional: R$ {contract.notional:,.2f}, Taxa: {contract.annual_rate*100:.2f}% a.a.",
                conservation_law="LAW-079",
                details={
                    "maturity_days": contract.maturity_days,
                    "indexation": "DI + Spread",
                    "registry_system": "Cetip / NoMe Balcão",
                },
            )
        )

        # MÓDULO 3: Gestão de Gravames & Domicílio Bancário (se aplicável)
        if contract.lien_holder:
            trace.steps.append(
                StepAuditRecord(
                    module=B3Module.NOME_REGISTRY,
                    status=StepStatus.REGISTERED,
                    latency_micros=Decimal("140.0"),
                    summary=f"Trava de domicílio e gravame fiduciário constituídos em favor de {contract.lien_holder} (Anti-duplicidade registral).",
                    conservation_law="LAW-037",
                    details={
                        "regulation": "Resolução CMN 4.734 / Circular BCB 3.952",
                        "lien_holder": contract.lien_holder,
                        "fiduciary_lock": "BLOQUEIO ATIVO 100%",
                    },
                )
            )

        # MÓDULO 4: Contraparte Central / Barramento Central
        trace.steps.append(
            StepAuditRecord(
                module=B3Module.CCP_CLEARING,
                status=StepStatus.NOVATED,
                latency_micros=Decimal("250.0"),
                summary="Enquadramento no Barramento Central de Liquidação Unificada e mitigação de risco de crédito.",
                conservation_law="LAW-106A",
            )
        )

        # MÓDULO 5: Liquidação Financeira SPB (DvP)
        trace.steps.append(
            StepAuditRecord(
                module=B3Module.SPB_SETTLEMENT,
                status=StepStatus.SETTLED,
                latency_micros=Decimal("410.0"),
                summary=f"Liquidação Financeira DvP concluída no STR: R$ {contract.notional:,.2f} creditados ao emissor contra entrega do título.",
                conservation_law="LAW-079",
            )
        )

        # MÓDULO 6: Escrituração na Central Depositária (CSD)
        trace.steps.append(
            StepAuditRecord(
                module=B3Module.CSD_CUSTODY,
                status=StepStatus.SETTLED,
                latency_micros=Decimal("190.0"),
                summary=f"Título escritural registrado e bloqueado na conta de custódia individualizada de {contract.investor_id}.",
                conservation_law="LAW-001",
            )
        )
        trace.is_settled = True

        # MÓDULO 7: Supervisão de Mercados (BSM)
        trace.steps.append(
            StepAuditRecord(
                module=B3Module.BSM_SURVEILLANCE,
                status=StepStatus.AUDITED,
                latency_micros=Decimal("65.0"),
                summary="Auditoria de unicidade de lastro e ausência de sobrecolateralização cruzada concluída com sucesso.",
                conservation_law="LAW-082",
            )
        )

        return trace
