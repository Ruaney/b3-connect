"""Motor de Auditoria Regulatória e Supervisão de Mercado BSM (FL-030).

Este módulo implementa os padrões determinísticos de trilha de auditoria para
a BSM Supervisão de Mercados (autorreguladora da B3 S.A.), incluindo:
1. Reconstituição cronológica de ordens e execuções com precisão de microsegundos.
2. Identificação do Beneficiário Final (CVM 35 / CVM 505), Operador Homologado e Algo ID.
3. Algoritmos analíticos de vigilância preventiva:
   - Wash Trading (operações cruzadas sem alteração de titularidade).
   - Spoofing (inserção e cancelamento sistemático de ordens para manipular o book).
   - Layering (inserção de múltiplas camadas de ordens fictícias).
   - Churning (giro excessivo de carteira para gerar corretagem).
   - Order-to-Trade Ratio (OTR) regulatório conforme metodologia oficial B3.
4. Geração de relatórios e dossiês de fiscalização para envio à BSM/CVM.

Zero dependências externas (apenas Python Standard Library).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union


# =====================================================================
# ENUMS E CONSTANTES NORMATIVAS BSM / B3
# =====================================================================

class AbusePatternType(str, Enum):
    """Padrões tipificados de conduta vedada e manipulação de mercado."""

    WASH_TRADING = "WASH_TRADING"  # Auto-negociação / Negociação fictícia
    SPOOFING = "SPOOFING"  # Manipulação por cancelamento sistemático
    LAYERING = "LAYERING"  # Manipulação por estratificação em camadas
    CHURNING = "CHURNING"  # Giro excessivo de carteira discricionária
    EXCESSIVE_OTR = "EXCESSIVE_OTR"  # Order-to-Trade Ratio acima do teto normativo


class ActionType(str, Enum):
    """Tipos de ação em ciclo de vida de ordem no PUMA Trading System."""

    NEW = "NEW"  # Inserção de nova oferta (NewOrderSingle)
    CANCEL = "CANCEL"  # Cancelamento de oferta (OrderCancelRequest)
    REPLACE = "REPLACE"  # Alteração de oferta (OrderCancelReplaceRequest)
    TRADE = "TRADE"  # Execução de negócio (ExecutionReport Fill/Partial)


class Side(str, Enum):
    """Lado da intenção de negociação."""

    BUY = "BUY"  # Compra (FIX Side=1)
    SELL = "SELL"  # Venda (FIX Side=2)


class BeneficiaryType(str, Enum):
    """Classificação cadastral do comitente final perante a CVM."""

    PF = "PF"  # Pessoa Física (CPF)
    PJ = "PJ"  # Pessoa Jurídica (CNPJ)
    INVESTMENT_FUND = "INVESTMENT_FUND"  # Fundo de Investimento (Resolução CVM 175)
    NON_RESIDENT = "NON_RESIDENT"  # Investidor Não Residente (Res. CMN 4.373)


class SeverityLevel(str, Enum):
    """Níveis de severidade para enquadramento de alertas de supervisão."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ComplianceStatus(str, Enum):
    """Status de conformidade do registro de auditoria."""

    CONFORME = "CONFORME"
    SUSPEITO = "SUSPEITO"
    IRREGULAR = "IRREGULAR"


# =====================================================================
# EXCEÇÕES DO MÓDULO BSM
# =====================================================================

class BsmSurveillanceError(Exception):
    """Exceção base para o subsistema de supervisão BSM."""


class BsmValidationError(BsmSurveillanceError):
    """Erro de validação cadastral ou estrutural na trilha de auditoria."""


class BsmAuditTrailError(BsmSurveillanceError):
    """Erro na cronologia ou integridade do log de auditoria."""


# =====================================================================
# FUNÇÕES UTILITÁRIAS DE NORMALIZAÇÃO E TEMPO
# =====================================================================

def normalize_document(doc: str) -> str:
    """Remove caracteres não numéricos de CPF/CNPJ."""
    if not doc:
        return ""
    return re.sub(r"\D", "", str(doc).strip())


def format_document(doc: str) -> str:
    """Formata documento numérico em CPF ou CNPJ legível."""
    digits = normalize_document(doc)
    if len(digits) == 11:
        return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"
    elif len(digits) == 14:
        return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"
    return doc


def parse_timestamp(ts: Union[str, datetime]) -> datetime:
    """Converte representação temporal ISO-8601 ou FIX para datetime UTC ciente."""
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)

    raw = str(ts).strip()
    # Padrão FIX: YYYYMMDD-HH:MM:SS.ffffff ou YYYYMMDD-HH:MM:SS
    if re.match(r"^\d{8}-\d{2}:\d{2}:\d{2}", raw):
        dt_str = raw.replace("-", "T", 1)
        # Se contiver apenas o hífen do date-time separator
        try:
            dt = datetime.strptime(raw, "%Y%m%d-%H:%M:%S.%f")
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            dt = datetime.strptime(raw, "%Y%m%d-%H:%M:%S")
            return dt.replace(tzinfo=timezone.utc)

    # Padrão ISO-8601
    clean = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception as err:
        raise BsmValidationError(f"Formato de timestamp inválido '{raw}': {err}")


# =====================================================================
# MODELOS DE DADOS DA TRILHA DE AUDITORIA
# =====================================================================

@dataclass
class AuditRecord:
    """Registro individual e auditável de evento de ordem ou execução na B3."""

    record_id: str
    timestamp: datetime
    participant_id: str
    clearing_member_id: str
    account_id: str
    beneficiary_id: str
    beneficiary_type: BeneficiaryType
    operator_id: str
    symbol: str
    side: Side
    price: Decimal
    quantity: int
    order_id: str
    cl_ord_id: str
    action_type: ActionType
    algo_id: Optional[str] = None
    execution_id: Optional[str] = None
    cvm_category: str = "CVM_35"
    compliance_status: ComplianceStatus = ComplianceStatus.CONFORME
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Valida e normaliza campos fundamentais do registro de auditoria."""
        if not self.record_id:
            raise BsmValidationError("record_id é obrigatório.")
        if not self.symbol:
            raise BsmValidationError("symbol é obrigatório.")
        if not self.participant_id:
            raise BsmValidationError("participant_id é obrigatório.")
        if not self.operator_id:
            raise BsmValidationError("operator_id é obrigatório para compliance PQO.")
        if not self.order_id:
            raise BsmValidationError("order_id é obrigatório.")

        # Normaliza timestamp
        self.timestamp = parse_timestamp(self.timestamp)

        # Normaliza tipos enumerados
        if isinstance(self.side, str):
            side_str = self.side.upper()
            if side_str in ("1", "BUY", "C", "COMPRA"):
                self.side = Side.BUY
            elif side_str in ("2", "SELL", "V", "VENDA"):
                self.side = Side.SELL
            else:
                try:
                    self.side = Side(side_str)
                except ValueError:
                    raise BsmValidationError(f"Lado inválido: '{self.side}'")

        if isinstance(self.action_type, str):
            act_str = self.action_type.upper()
            try:
                self.action_type = ActionType(act_str)
            except ValueError:
                raise BsmValidationError(f"action_type inválido: '{self.action_type}'")

        if isinstance(self.beneficiary_type, str):
            try:
                self.beneficiary_type = BeneficiaryType(self.beneficiary_type.upper())
            except ValueError:
                self.beneficiary_type = BeneficiaryType.PF

        if isinstance(self.compliance_status, str):
            try:
                self.compliance_status = ComplianceStatus(self.compliance_status.upper())
            except ValueError:
                self.compliance_status = ComplianceStatus.CONFORME

        # Normaliza documento
        self.beneficiary_id = normalize_document(self.beneficiary_id)
        if not self.beneficiary_id:
            raise BsmValidationError("beneficiary_id (CPF/CNPJ) é obrigatório pela CVM 35.")

        # Normaliza valores numéricos
        if not isinstance(self.price, Decimal):
            self.price = Decimal(str(self.price))
        if self.price < Decimal("0"):
            raise BsmValidationError(f"Preço não pode ser negativo: {self.price}")

        self.quantity = int(self.quantity)
        if self.quantity <= 0:
            raise BsmValidationError(f"Quantidade deve ser estritamente positiva: {self.quantity}")

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o registro para dicionário JSON-compatível."""
        data = asdict(self)
        data["timestamp"] = self.timestamp.isoformat()
        data["price"] = float(self.price)
        data["side"] = self.side.value
        data["action_type"] = self.action_type.value
        data["beneficiary_type"] = self.beneficiary_type.value
        data["compliance_status"] = self.compliance_status.value
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AuditRecord:
        """Instancia AuditRecord a partir de dicionário."""
        d = dict(data)
        return cls(
            record_id=str(d.get("record_id", "")),
            timestamp=parse_timestamp(d.get("timestamp", datetime.now(timezone.utc))),
            participant_id=str(d.get("participant_id", "")),
            clearing_member_id=str(d.get("clearing_member_id", "")),
            account_id=str(d.get("account_id", "")),
            beneficiary_id=str(d.get("beneficiary_id", "")),
            beneficiary_type=d.get("beneficiary_type", BeneficiaryType.PF),
            operator_id=str(d.get("operator_id", "")),
            symbol=str(d.get("symbol", "")),
            side=d.get("side", Side.BUY),
            price=Decimal(str(d.get("price", "0.0"))),
            quantity=int(d.get("quantity", 0)),
            order_id=str(d.get("order_id", "")),
            cl_ord_id=str(d.get("cl_ord_id", "")),
            action_type=d.get("action_type", ActionType.NEW),
            algo_id=d.get("algo_id"),
            execution_id=d.get("execution_id"),
            cvm_category=str(d.get("cvm_category", "CVM_35")),
            compliance_status=d.get("compliance_status", ComplianceStatus.CONFORME),
            metadata=d.get("metadata", {}) or {},
        )


@dataclass
class SurveillanceAlert:
    """Alerta analítico de supervisão gerado por detecção de padrão vedado."""

    alert_id: str
    pattern_type: AbusePatternType
    severity: SeverityLevel
    symbol: str
    participant_id: str
    beneficiary_id: Optional[str]
    operator_id: Optional[str]
    algo_id: Optional[str]
    timestamp: datetime
    description: str
    metrics: Dict[str, Any] = field(default_factory=dict)
    evidence_records: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o alerta para dicionário JSON."""
        return {
            "alert_id": self.alert_id,
            "pattern_type": self.pattern_type.value,
            "severity": self.severity.value,
            "symbol": self.symbol,
            "participant_id": self.participant_id,
            "beneficiary_id": self.beneficiary_id,
            "beneficiary_formatted": format_document(self.beneficiary_id) if self.beneficiary_id else None,
            "operator_id": self.operator_id,
            "algo_id": self.algo_id,
            "timestamp": self.timestamp.isoformat(),
            "description": self.description,
            "metrics": self.metrics,
            "evidence_records": self.evidence_records,
        }


@dataclass
class BsmAuditReport:
    """Relatório executivo consolidado de auditoria e vigilância BSM."""

    report_id: str
    generated_at: datetime
    total_records: int
    total_orders: int
    total_trades: int
    total_cancels: int
    total_replaces: int
    global_otr: float
    alerts_count: int
    alerts: List[SurveillanceAlert]
    participant_summaries: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o relatório consolidado."""
        return {
            "report_id": self.report_id,
            "generated_at": self.generated_at.isoformat(),
            "total_records": self.total_records,
            "total_orders": self.total_orders,
            "total_trades": self.total_trades,
            "total_cancels": self.total_cancels,
            "total_replaces": self.total_replaces,
            "global_otr": round(self.global_otr, 4),
            "alerts_count": self.alerts_count,
            "alerts": [a.to_dict() for a in self.alerts],
            "participant_summaries": self.participant_summaries,
        }

    def to_json(self, indent: int = 2) -> str:
        """Gera representação textual JSON."""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


# =====================================================================
# MOTOR DE SUPERVISÃO E DETECÇÃO ANALÍTICA BSM
# =====================================================================

class BsmSurveillanceEngine:
    """Motor analítico de supervisão de mercados e detecção de abusos BSM."""

    def __init__(self, records: Optional[Sequence[AuditRecord]] = None) -> None:
        self._records: List[AuditRecord] = []
        if records:
            self.ingest_records(records)

    @property
    def records(self) -> List[AuditRecord]:
        """Retorna os registros de auditoria ordenados cronologicamente."""
        return list(self._records)

    def ingest_record(self, record: Union[AuditRecord, Dict[str, Any]]) -> AuditRecord:
        """Ingere e valida um registro individual de auditoria."""
        if isinstance(record, dict):
            rec = AuditRecord.from_dict(record)
        elif isinstance(record, AuditRecord):
            rec = record
        else:
            raise BsmValidationError(f"Tipo inválido para registro: {type(record)}")

        self._records.append(rec)
        # Mantém registros ordenados por timestamp
        self._records.sort(key=lambda r: r.timestamp)
        return rec

    def ingest_records(
        self, records: Sequence[Union[AuditRecord, Dict[str, Any]]]
    ) -> List[AuditRecord]:
        """Ingere um lote de registros na trilha de auditoria."""
        added = [self.ingest_record(r) for r in records]
        return added

    def load_from_json(self, path_or_str: Union[str, Path]) -> List[AuditRecord]:
        """Carrega registros a partir de arquivo ou string JSON."""
        path = Path(path_or_str)
        if path.exists() and path.is_file():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = json.loads(str(path_or_str))

        if not isinstance(data, list):
            raise BsmValidationError("Conteúdo JSON deve ser uma lista de registros de auditoria.")

        return self.ingest_records(data)

    # -----------------------------------------------------------------
    # DETECTOR 1: WASH TRADING (Auto-Negociação)
    # -----------------------------------------------------------------
    def detect_wash_trading(
        self, time_window_seconds: float = 60.0
    ) -> List[SurveillanceAlert]:
        """Detecta operações cruzadas onde comprador e vendedor possuem mesmo CPF/CNPJ.

        Avalia tanto execuções registradas quanto ordens simultâneas de compra e venda
        com o mesmo beneficiário final ou mesma conta para o mesmo instrumento e preço.
        """
        alerts: List[SurveillanceAlert] = []
        alert_counter = 1

        # 1. Agrupamento por símbolo
        by_symbol: Dict[str, List[AuditRecord]] = {}
        for r in self._records:
            by_symbol.setdefault(r.symbol, []).append(r)

        for symbol, recs in by_symbol.items():
            # Separa compras e vendas
            buys = [r for r in recs if r.side == Side.BUY]
            sells = [r for r in recs if r.side == Side.SELL]

            for b in buys:
                for s in sells:
                    # Verifica mesmo beneficiário final ou mesma conta de custódia
                    same_beneficiary = b.beneficiary_id and (b.beneficiary_id == s.beneficiary_id)
                    same_account = b.account_id and (b.account_id == s.account_id)

                    if not (same_beneficiary or same_account):
                        continue

                    # Verifica janela temporal
                    delta = abs((b.timestamp - s.timestamp).total_seconds())
                    if delta > time_window_seconds:
                        continue

                    # Verifica cruzamento de preços (Buy Price >= Sell Price) ou igualdade
                    # Se for TRADE ou NEW ordens que se cruzariam no book
                    price_cross = (b.price >= s.price)

                    if price_cross:
                        # Se já for TRADE ou ordens com intenção de fechamento
                        is_trade = (b.action_type == ActionType.TRADE or s.action_type == ActionType.TRADE)
                        severity = SeverityLevel.CRITICAL if is_trade else SeverityLevel.HIGH

                        alert = SurveillanceAlert(
                            alert_id=f"ALT-WASH-{symbol}-{alert_counter:04d}",
                            pattern_type=AbusePatternType.WASH_TRADING,
                            severity=severity,
                            symbol=symbol,
                            participant_id=b.participant_id,
                            beneficiary_id=b.beneficiary_id,
                            operator_id=b.operator_id or s.operator_id,
                            algo_id=b.algo_id or s.algo_id,
                            timestamp=max(b.timestamp, s.timestamp),
                            description=(
                                f"Detecção de Wash Trading em {symbol}: ordens opostas ({b.side.value} vs {s.side.value}) "
                                f"com mesmo beneficiário final ({format_document(b.beneficiary_id)}) "
                                f"executadas/cruzadas em intervalo de {delta:.3f}s a preços compatíveis "
                                f"(Compra={b.price}, Venda={s.price})."
                            ),
                            metrics={
                                "buy_record_id": b.record_id,
                                "sell_record_id": s.record_id,
                                "buy_order_id": b.order_id,
                                "sell_order_id": s.order_id,
                                "delta_seconds": round(delta, 6),
                                "buy_price": float(b.price),
                                "sell_price": float(s.price),
                                "buy_qty": b.quantity,
                                "sell_qty": s.quantity,
                                "is_trade_execution": is_trade,
                            },
                            evidence_records=[b.record_id, s.record_id],
                        )
                        alerts.append(alert)
                        alert_counter += 1

        return alerts

    # -----------------------------------------------------------------
    # DETECTOR 2: SPOOFING E ORDER-TO-TRADE RATIO (OTR)
    # -----------------------------------------------------------------
    def detect_spoofing_and_excessive_otr(
        self,
        otr_threshold: float = 20.0,
        min_messages: int = 5,
        cancel_ratio_threshold: float = 0.70,
    ) -> List[SurveillanceAlert]:
        """Calcula OTR oficial B3 e detecta spoofing/cancelamento desproporcional.

        Identifica participantes/algoritmos/beneficiários que emitem grande volume
        de ofertas e as cancelam sistematicamente sem gerar negócios proporcionais.
        """
        alerts: List[SurveillanceAlert] = []
        alert_counter = 1

        # Agrupa por (participant_id, symbol, beneficiary_id, operator_id, algo_id)
        grouped: Dict[Tuple[str, str, str, str, Optional[str]], List[AuditRecord]] = {}
        for r in self._records:
            key = (r.participant_id, r.symbol, r.beneficiary_id, r.operator_id, r.algo_id)
            grouped.setdefault(key, []).append(r)

        for (part_id, sym, ben_id, oper_id, algo_id), recs in grouped.items():
            n_new = sum(1 for r in recs if r.action_type == ActionType.NEW)
            n_cancel = sum(1 for r in recs if r.action_type == ActionType.CANCEL)
            n_replace = sum(1 for r in recs if r.action_type == ActionType.REPLACE)
            n_trade = sum(1 for r in recs if r.action_type == ActionType.TRADE)

            total_messages = n_new + n_cancel + n_replace
            if total_messages < min_messages:
                continue

            # Cálculo de OTR Normativo B3:
            # OTR = (New + Cancel + Replace) / max(1, Trade)
            otr = total_messages / max(1, n_trade)
            cancel_ratio = n_cancel / max(1, n_new)

            qty_ordered = sum(r.quantity for r in recs if r.action_type == ActionType.NEW)
            qty_cancelled = sum(r.quantity for r in recs if r.action_type == ActionType.CANCEL)
            qty_traded = sum(r.quantity for r in recs if r.action_type == ActionType.TRADE)
            otr_volume = (qty_ordered + qty_cancelled) / max(1, qty_traded)

            is_excessive_otr = otr >= otr_threshold
            is_spoofing = is_excessive_otr and (cancel_ratio >= cancel_ratio_threshold)

            if is_spoofing or is_excessive_otr:
                pattern = AbusePatternType.SPOOFING if is_spoofing else AbusePatternType.EXCESSIVE_OTR

                # Define severidade conforme faixas BSM
                if otr > 100.0 or (is_spoofing and cancel_ratio >= 0.85):
                    severity = SeverityLevel.CRITICAL
                elif otr > 50.0:
                    severity = SeverityLevel.HIGH
                else:
                    severity = SeverityLevel.MEDIUM

                desc = (
                    f"Alerta de {pattern.value} em {sym} para operador {oper_id} / participante {part_id}: "
                    f"OTR apurado de {otr:.2f} (limite {otr_threshold}), taxa de cancelamento de "
                    f"{cancel_ratio * 100:.1f}%. Mensagens: {total_messages} (Novas={n_new}, Cancels={n_cancel}, Trades={n_trade})."
                )

                evidence = [r.record_id for r in recs]

                alert = SurveillanceAlert(
                    alert_id=f"ALT-OTR-{sym}-{alert_counter:04d}",
                    pattern_type=pattern,
                    severity=severity,
                    symbol=sym,
                    participant_id=part_id,
                    beneficiary_id=ben_id,
                    operator_id=oper_id,
                    algo_id=algo_id,
                    timestamp=recs[-1].timestamp,
                    description=desc,
                    metrics={
                        "otr": round(otr, 2),
                        "otr_volume": round(otr_volume, 2),
                        "otr_threshold": otr_threshold,
                        "n_orders": n_new,
                        "n_cancels": n_cancel,
                        "n_replaces": n_replace,
                        "n_trades": n_trade,
                        "cancel_ratio": round(cancel_ratio, 4),
                        "qty_ordered": qty_ordered,
                        "qty_cancelled": qty_cancelled,
                        "qty_traded": qty_traded,
                    },
                    evidence_records=evidence,
                )
                alerts.append(alert)
                alert_counter += 1

        return alerts

    # -----------------------------------------------------------------
    # DETECTOR 3: LAYERING (Estratificação em Múltiplas Camadas)
    # -----------------------------------------------------------------
    def detect_layering(
        self, min_layers: int = 3, time_window_seconds: float = 15.0
    ) -> List[SurveillanceAlert]:
        """Detecta inserção de múltiplas camadas de ordens no mesmo lado com cancelamento em bloco."""
        alerts: List[SurveillanceAlert] = []
        alert_counter = 1

        # Agrupa por (symbol, participant_id, beneficiary_id, side)
        grouped: Dict[Tuple[str, str, str, Side], List[AuditRecord]] = {}
        for r in self._records:
            key = (r.symbol, r.participant_id, r.beneficiary_id, r.side)
            grouped.setdefault(key, []).append(r)

        for (sym, part_id, ben_id, side), recs in grouped.items():
            new_orders = [r for r in recs if r.action_type == ActionType.NEW]
            cancel_orders = [r for r in recs if r.action_type == ActionType.CANCEL]

            if len(new_orders) < min_layers or len(cancel_orders) < min_layers:
                continue

            # Checa se ordens novas têm preços distintos (camadas escalonadas)
            distinct_prices = {r.price for r in new_orders}
            if len(distinct_prices) >= min_layers:
                # Checa janela temporal do bloco
                start_time = min(r.timestamp for r in new_orders)
                end_time = max(r.timestamp for r in cancel_orders)
                duration = (end_time - start_time).total_seconds()

                if duration <= time_window_seconds:
                    alert = SurveillanceAlert(
                        alert_id=f"ALT-LAYER-{sym}-{alert_counter:04d}",
                        pattern_type=AbusePatternType.LAYERING,
                        severity=SeverityLevel.HIGH,
                        symbol=sym,
                        participant_id=part_id,
                        beneficiary_id=ben_id,
                        operator_id=new_orders[0].operator_id,
                        algo_id=new_orders[0].algo_id,
                        timestamp=end_time,
                        description=(
                            f"Detecção de Layering em {sym} ({side.value}): {len(distinct_prices)} camadas "
                            f"de preços distintos inseridas e canceladas em {duration:.2f}s sem execução."
                        ),
                        metrics={
                            "layer_count": len(distinct_prices),
                            "prices": [float(p) for p in sorted(distinct_prices)],
                            "duration_seconds": round(duration, 3),
                            "side": side.value,
                        },
                        evidence_records=[r.record_id for r in new_orders + cancel_orders],
                    )
                    alerts.append(alert)
                    alert_counter += 1

        return alerts

    # -----------------------------------------------------------------
    # DETECTOR 4: CHURNING (Giro Excessivo de Carteira)
    # -----------------------------------------------------------------
    def detect_churning(
        self, turnover_threshold: float = 5.0, min_trades: int = 6
    ) -> List[SurveillanceAlert]:
        """Detecta giro excessivo de ordens com alteração desprezível de posição líquida."""
        alerts: List[SurveillanceAlert] = []
        alert_counter = 1

        # Agrupa trades por (account_id, beneficiary_id, symbol)
        grouped: Dict[Tuple[str, str, str], List[AuditRecord]] = {}
        for r in self._records:
            if r.action_type == ActionType.TRADE:
                key = (r.account_id, r.beneficiary_id, r.symbol)
                grouped.setdefault(key, []).append(r)

        for (acc_id, ben_id, sym), trades in grouped.items():
            if len(trades) < min_trades:
                continue

            buy_qty = sum(t.quantity for t in trades if t.side == Side.BUY)
            sell_qty = sum(t.quantity for t in trades if t.side == Side.SELL)
            gross_qty = buy_qty + sell_qty
            net_qty = abs(buy_qty - sell_qty)

            # Relação de giro vs alteração líquida
            if gross_qty > 0:
                net_change_ratio = net_qty / gross_qty
                # Se girou muito (> min_trades) mas a carteira líquida mudou menos de 15%
                if net_change_ratio < 0.15:
                    alert = SurveillanceAlert(
                        alert_id=f"ALT-CHURN-{sym}-{alert_counter:04d}",
                        pattern_type=AbusePatternType.CHURNING,
                        severity=SeverityLevel.HIGH,
                        symbol=sym,
                        participant_id=trades[0].participant_id,
                        beneficiary_id=ben_id,
                        operator_id=trades[0].operator_id,
                        algo_id=trades[0].algo_id,
                        timestamp=max(t.timestamp for t in trades),
                        description=(
                            f"Detecção de Churning na conta {acc_id} ({sym}): {len(trades)} negócios realizados "
                            f"(Volume Bruto={gross_qty}), com variação líquida de apenas {net_qty} títulos "
                            f"({net_change_ratio * 100:.1f}% da rotação)."
                        ),
                        metrics={
                            "total_trades": len(trades),
                            "buy_qty": buy_qty,
                            "sell_qty": sell_qty,
                            "gross_qty": gross_qty,
                            "net_qty": net_qty,
                            "net_change_ratio": round(net_change_ratio, 4),
                        },
                        evidence_records=[t.record_id for t in trades],
                    )
                    alerts.append(alert)
                    alert_counter += 1

        return alerts

    # -----------------------------------------------------------------
    # EXECUÇÃO INTEGRADA DE TODOS OS DETECTORES
    # -----------------------------------------------------------------
    def run_all_detectors(
        self,
        otr_threshold: float = 20.0,
        cancel_ratio_threshold: float = 0.70,
        wash_window_seconds: float = 60.0,
    ) -> List[SurveillanceAlert]:
        """Executa toda a suíte de vigilância preventiva BSM e retorna alertas consolidados."""
        alerts: List[SurveillanceAlert] = []
        alerts.extend(self.detect_wash_trading(time_window_seconds=wash_window_seconds))
        alerts.extend(
            self.detect_spoofing_and_excessive_otr(
                otr_threshold=otr_threshold,
                cancel_ratio_threshold=cancel_ratio_threshold,
            )
        )
        alerts.extend(self.detect_layering())
        alerts.extend(self.detect_churning())
        return alerts

    # -----------------------------------------------------------------
    # RELATÓRIOS E DOSSIÊ DE AUDITORIA REGULATÓRIA BSM
    # -----------------------------------------------------------------
    def generate_audit_report(
        self, report_id: Optional[str] = None
    ) -> BsmAuditReport:
        """Gera relatório de auditoria e métricas de supervisão BSM."""
        now = datetime.now(timezone.utc)
        rid = report_id or f"BSM-REP-{now.strftime('%Y%m%d-%H%M%S')}"

        total_records = len(self._records)
        total_orders = sum(1 for r in self._records if r.action_type == ActionType.NEW)
        total_cancels = sum(1 for r in self._records if r.action_type == ActionType.CANCEL)
        total_replaces = sum(1 for r in self._records if r.action_type == ActionType.REPLACE)
        total_trades = sum(1 for r in self._records if r.action_type == ActionType.TRADE)

        msg_count = total_orders + total_cancels + total_replaces
        global_otr = msg_count / max(1, total_trades)

        alerts = self.run_all_detectors()

        # Resumos por participante
        summaries: Dict[str, Dict[str, Any]] = {}
        for r in self._records:
            pid = r.participant_id
            if pid not in summaries:
                summaries[pid] = {
                    "orders": 0,
                    "cancels": 0,
                    "replaces": 0,
                    "trades": 0,
                    "total_qty": 0,
                }
            if r.action_type == ActionType.NEW:
                summaries[pid]["orders"] += 1
            elif r.action_type == ActionType.CANCEL:
                summaries[pid]["cancels"] += 1
            elif r.action_type == ActionType.REPLACE:
                summaries[pid]["replaces"] += 1
            elif r.action_type == ActionType.TRADE:
                summaries[pid]["trades"] += 1
            summaries[pid]["total_qty"] += r.quantity

        return BsmAuditReport(
            report_id=rid,
            generated_at=now,
            total_records=total_records,
            total_orders=total_orders,
            total_trades=total_trades,
            total_cancels=total_cancels,
            total_replaces=total_replaces,
            global_otr=global_otr,
            alerts_count=len(alerts),
            alerts=alerts,
            participant_summaries=summaries,
        )

    def export_bsm_dossier(self, alert: SurveillanceAlert) -> Dict[str, Any]:
        """Exporta dossiê técnico de fiscalização para envio à diretoria de supervisão da BSM."""
        evidence_records = [
            r.to_dict() for r in self._records if r.record_id in alert.evidence_records
        ]

        return {
            "dossier_version": "1.0.0",
            "entity": "BSM Supervisão de Mercados - B3 S.A.",
            "protocol_id": f"BSM-DOSSIER-{alert.alert_id}",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "alert": alert.to_dict(),
            "participant": {
                "participant_id": alert.participant_id,
                "operator_id": alert.operator_id,
                "algo_id": alert.algo_id,
            },
            "beneficiary": {
                "beneficiary_id": alert.beneficiary_id,
                "formatted": format_document(alert.beneficiary_id) if alert.beneficiary_id else None,
            },
            "evidence_count": len(evidence_records),
            "evidence_chain": evidence_records,
        }
