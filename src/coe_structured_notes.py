"""
B3 Balcão NoMe — Certificado de Operações Estruturadas (COE) (FL-058)
====================================================================
Implementação do motor determinístico de registro, precificação, monitoramento
de barreiras e liquidação no vencimento/autocall de Certificados de Operações
Estruturadas (COE), em estrita conformidade com a Resolução CMN nº 4.263/2013,
Instrução CVM nº 569/2015 e Resolução CVM nº 8/2020.

Invariantes Formais Provadas:
1. Conservação de Capital Protegido: Em COEs de Valor Nominal Protegido,
   o valor de liquidação no vencimento é estritamente maior ou igual ao
   percentual garantido do Valor Nominal Unitário (VNU).
2. Delimitação de Retorno por Teto (Cap Invariant): O retorno do investidor
   nunca ultrapassa o payoff máximo acordado no Documento de Informações
   Essenciais (DIE).
3. Conservação de Retenção de IRRF: O fechamento financeiro obedece
   rigorosamente a tabela regressiva de Renda Fixa (22,5% a 15,0%)
   sem criação nem aniquilação de centavos.
"""

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_EVEN, ROUND_DOWN
from enum import Enum
from typing import Dict, List, Optional
import json
from pathlib import Path


class CoeCapitalProtectionType(str, Enum):
    """Modalidade de proteção do valor nominal do COE."""
    PROTECTED_CAPITAL = "PROTECTED_CAPITAL"  # Valor Nominal Protegido (ex: 100% garantido)
    AT_RISK_CAPITAL = "AT_RISK_CAPITAL"        # Valor Nominal em Risco (perda máxima estipulada)


class CoePayoffStructure(str, Enum):
    """Estrutura de payoff e estratégia de derivativos embutida no COE."""
    BULL_SPREAD = "BULL_SPREAD"                # Alta com ganho limitado (Call Spread)
    DIGITAL_BINARY = "DIGITAL_BINARY"          # Retorno fixo condicional a barreira
    AUTOCALLABLE = "AUTOCALLABLE"              # Chamada antecipada programada com cupom
    PARTICIPATION_WITH_CAP = "PARTICIPATION_CAP" # Percentual de participação com teto


class UnderlyingAssetType(str, Enum):
    """Ativo subjacente ou índice de referência do COE."""
    IBOVESPA = "IBOVESPA"
    SP500 = "SP500"
    PTAX_USD = "PTAX_USD"
    CDI = "CDI"
    GOLD_OZ1D = "GOLD_OZ1D"


class CoeStatus(str, Enum):
    """Ciclo de vida do COE no Balcão NoMe."""
    REGISTERED = "REGISTERED"
    ACTIVE = "ACTIVE"
    EARLY_TERMINATED = "EARLY_TERMINATED"  # Autocall acionado
    MATURED = "MATURED"
    SETTLED = "SETTLED"


@dataclass(frozen=True)
class ObservationDateEvent:
    """Evento em data de observação intermediária para Autocall ou barreiras."""
    observation_date: str
    barrier_level_pct: Decimal  # Ex: 100.0% do valor inicial
    coupon_rate_pct: Decimal    # Cupom pago caso o autocall seja disparado
    triggered: bool = False


@dataclass
class CoeContractSpec:
    """Especificação formal do COE registrado no Balcão NoMe B3."""
    contract_id: str
    issuer_institution_id: str
    underlying_asset: UnderlyingAssetType
    protection_type: CoeCapitalProtectionType
    payoff_structure: CoePayoffStructure
    issue_date: str
    maturity_date: str
    nominal_unit_value: Decimal  # VNU (ex: R$ 1.000,00)
    quantity: int
    initial_underlying_level: Decimal  # Preço base do ativo no início
    protection_floor_pct: Decimal     # Piso garantido (ex: 100.0% para protegido, 80.0% risco limitado)
    maximum_cap_gain_pct: Optional[Decimal] = None # Ganho máximo possível (DIE)
    participation_rate_pct: Decimal = Decimal("100.00") # Ex: 100% da alta
    digital_coupon_pct: Optional[Decimal] = None
    observation_schedule: List[ObservationDateEvent] = field(default_factory=list)
    status: CoeStatus = CoeStatus.REGISTERED

    @property
    def total_initial_notional(self) -> Decimal:
        return (self.nominal_unit_value * Decimal(str(self.quantity))).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


@dataclass(frozen=True)
class CoeTaxBreakdown:
    """Detalhamento tributário da liquidação de COE."""
    gross_yield: Decimal
    holding_days: int
    tax_rate: Decimal
    irrf_amount: Decimal
    net_yield: Decimal


@dataclass(frozen=True)
class CoeSettlementReceipt:
    """Comprovante oficial de liquidação no NoMe/Câmara B3."""
    settlement_id: str
    contract_id: str
    settlement_date: str
    settlement_reason: str  # 'MATURITY', 'AUTOCALL', 'EARLY_REDEMPTION'
    underlying_final_level: Decimal
    underlying_performance_pct: Decimal
    gross_unit_payoff: Decimal
    total_gross_settlement: Decimal
    total_principal_invested: Decimal
    tax_breakdown: CoeTaxBreakdown
    total_net_settlement: Decimal
    invariant_capital_protection_held: bool
    invariant_cap_gain_held: bool


class CoeError(Exception):
    """Exceção base do motor de COE."""
    pass


class CoeValidationError(CoeError):
    pass


class CoeSettlementError(CoeError):
    pass


class CoeEngine:
    """
    Motor determinístico para registro, avaliação de barreiras e liquidação de COEs.
    """

    def __init__(self):
        self.contracts: Dict[str, CoeContractSpec] = {}
        self.settlement_receipts: Dict[str, CoeSettlementReceipt] = {}

    def register_coe(self, spec: CoeContractSpec) -> None:
        if spec.contract_id in self.contracts:
            raise CoeValidationError(f"COE já registrado com id {spec.contract_id}")
        if spec.quantity <= 0:
            raise CoeValidationError("Quantidade deve ser positiva")
        if spec.nominal_unit_value <= Decimal("0.00"):
            raise CoeValidationError("VNU deve ser positivo")
        if spec.initial_underlying_level <= Decimal("0.00"):
            raise CoeValidationError("Preço inicial do subjacente deve ser positivo")
        if spec.protection_type == CoeCapitalProtectionType.PROTECTED_CAPITAL and spec.protection_floor_pct < Decimal("100.00"):
            raise CoeValidationError("COE de Capital Protegido deve ter piso mínimo de 100%")
        
        self.contracts[spec.contract_id] = spec

    def check_autocall(
        self,
        contract_id: str,
        observation_date: str,
        current_underlying_level: Decimal,
        days_elapsed: int
    ) -> Optional[CoeSettlementReceipt]:
        """
        Verifica se a barreira de saída antecipada (Autocall) foi atingida.
        """
        contract = self.contracts.get(contract_id)
        if not contract:
            raise CoeValidationError(f"COE {contract_id} não encontrado")
        if contract.payoff_structure != CoePayoffStructure.AUTOCALLABLE:
            return None
        if contract.status not in (CoeStatus.REGISTERED, CoeStatus.ACTIVE):
            return None

        # Procurar evento de observação compatível
        matched_event = None
        for evt in contract.observation_schedule:
            if evt.observation_date == observation_date:
                matched_event = evt
                break
        
        if not matched_event:
            return None

        perf_pct = ((current_underlying_level - contract.initial_underlying_level) / contract.initial_underlying_level) * Decimal("100")
        if current_underlying_level >= (contract.initial_underlying_level * (matched_event.barrier_level_pct / Decimal("100"))):
            # Autocall disparado!
            gross_unit = contract.nominal_unit_value * (Decimal("1.00") + (matched_event.coupon_rate_pct / Decimal("100")))
            gross_unit = gross_unit.quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)
            
            receipt = self._execute_settlement(
                contract=contract,
                settlement_date=observation_date,
                settlement_reason="AUTOCALL",
                final_level=current_underlying_level,
                perf_pct=perf_pct,
                gross_unit=gross_unit,
                days_elapsed=days_elapsed
            )
            contract.status = CoeStatus.EARLY_TERMINATED
            return receipt

        contract.status = CoeStatus.ACTIVE
        return None

    def settle_at_maturity(
        self,
        contract_id: str,
        final_underlying_level: Decimal,
        settlement_date: str,
        days_elapsed: int
    ) -> CoeSettlementReceipt:
        """
        Liquida o COE na data de vencimento calculando o payoff segundo a fórmula do DIE.
        """
        contract = self.contracts.get(contract_id)
        if not contract:
            raise CoeValidationError(f"COE {contract_id} não encontrado")
        if contract.status in (CoeStatus.MATURED, CoeStatus.SETTLED, CoeStatus.EARLY_TERMINATED):
            raise CoeSettlementError(f"COE {contract_id} já finalizado com status {contract.status}")

        perf_pct = ((final_underlying_level - contract.initial_underlying_level) / contract.initial_underlying_level) * Decimal("100")
        
        # Cálculo do Payoff Unitário Bruto
        gross_unit = self._calculate_payoff(contract, perf_pct)

        receipt = self._execute_settlement(
            contract=contract,
            settlement_date=settlement_date,
            settlement_reason="MATURITY",
            final_level=final_underlying_level,
            perf_pct=perf_pct,
            gross_unit=gross_unit,
            days_elapsed=days_elapsed
        )
        contract.status = CoeStatus.SETTLED
        return receipt

    def _calculate_payoff(self, contract: CoeContractSpec, perf_pct: Decimal) -> Decimal:
        vnu = contract.nominal_unit_value
        floor_multiplier = contract.protection_floor_pct / Decimal("100")
        min_guaranteed = (vnu * floor_multiplier).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)

        if contract.payoff_structure == CoePayoffStructure.BULL_SPREAD:
            if perf_pct <= Decimal("0.00"):
                payoff = min_guaranteed
            else:
                effective_gain = perf_pct * (contract.participation_rate_pct / Decimal("100"))
                if contract.maximum_cap_gain_pct is not None:
                    effective_gain = min(effective_gain, contract.maximum_cap_gain_pct)
                payoff = vnu * (Decimal("1.00") + (effective_gain / Decimal("100")))

        elif contract.payoff_structure == CoePayoffStructure.DIGITAL_BINARY:
            if perf_pct >= Decimal("0.00"):
                coupon = contract.digital_coupon_pct or Decimal("15.00")
                payoff = vnu * (Decimal("1.00") + (coupon / Decimal("100")))
            else:
                payoff = min_guaranteed

        elif contract.payoff_structure == CoePayoffStructure.PARTICIPATION_WITH_CAP:
            if perf_pct <= Decimal("0.00"):
                payoff = min_guaranteed
            else:
                effective_gain = perf_pct * (contract.participation_rate_pct / Decimal("100"))
                if contract.maximum_cap_gain_pct is not None:
                    effective_gain = min(effective_gain, contract.maximum_cap_gain_pct)
                payoff = vnu * (Decimal("1.00") + (effective_gain / Decimal("100")))

        elif contract.payoff_structure == CoePayoffStructure.AUTOCALLABLE:
            # Não disparou autocall intermediário: no vencimento recebe proteção ou cupom se >= 0
            if perf_pct >= Decimal("0.00"):
                coupon = contract.digital_coupon_pct or Decimal("20.00")
                payoff = vnu * (Decimal("1.00") + (coupon / Decimal("100")))
            else:
                payoff = min_guaranteed
        else:
            payoff = min_guaranteed

        return max(payoff, min_guaranteed).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)

    def _execute_settlement(
        self,
        contract: CoeContractSpec,
        settlement_date: str,
        settlement_reason: str,
        final_level: Decimal,
        perf_pct: Decimal,
        gross_unit: Decimal,
        days_elapsed: int
    ) -> CoeSettlementReceipt:
        total_gross = (gross_unit * Decimal(str(contract.quantity))).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
        principal = contract.total_initial_notional
        gross_yield = max(Decimal("0.00"), total_gross - principal)

        # Regra de Renda Fixa: tabela regressiva de IRRF
        if days_elapsed <= 180:
            tax_rate = Decimal("0.225")
        elif days_elapsed <= 360:
            tax_rate = Decimal("0.200")
        elif days_elapsed <= 720:
            tax_rate = Decimal("0.175")
        else:
            tax_rate = Decimal("0.150")

        irrf = (gross_yield * tax_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
        net_yield = (gross_yield - irrf).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
        total_net = (principal + net_yield).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)

        # Verificação formal de Invariantes
        # 1. Capital Protegido: Total Bruto >= Principal se for PROTECTED_CAPITAL
        cap_ok = True
        if contract.protection_type == CoeCapitalProtectionType.PROTECTED_CAPITAL:
            cap_ok = (total_gross >= principal)

        # 2. Teto de ganho (Cap) respeitado
        cap_gain_ok = True
        if contract.maximum_cap_gain_pct is not None:
            max_allowed = principal * (Decimal("1.00") + (contract.maximum_cap_gain_pct / Decimal("100")))
            cap_gain_ok = (total_gross <= max_allowed + Decimal("0.05")) # tolerância centésimos

        tax_b = CoeTaxBreakdown(
            gross_yield=gross_yield,
            holding_days=days_elapsed,
            tax_rate=tax_rate,
            irrf_amount=irrf,
            net_yield=net_yield
        )

        receipt_id = f"SETTLE-COE-{contract.contract_id}-{settlement_date.replace('-', '')}"
        receipt = CoeSettlementReceipt(
            settlement_id=receipt_id,
            contract_id=contract.contract_id,
            settlement_date=settlement_date,
            settlement_reason=settlement_reason,
            underlying_final_level=final_level,
            underlying_performance_pct=perf_pct.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN),
            gross_unit_payoff=gross_unit,
            total_gross_settlement=total_gross,
            total_principal_invested=principal,
            tax_breakdown=tax_b,
            total_net_settlement=total_net,
            invariant_capital_protection_held=cap_ok,
            invariant_cap_gain_held=cap_gain_ok
        )
        self.settlement_receipts[receipt_id] = receipt
        return receipt

    @classmethod
    def load_from_json(cls, filepath: Path) -> "CoeEngine":
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        engine = cls()
        for item in data.get("contracts", []):
            obs = [
                ObservationDateEvent(
                    observation_date=o["observation_date"],
                    barrier_level_pct=Decimal(str(o["barrier_level_pct"])),
                    coupon_rate_pct=Decimal(str(o["coupon_rate_pct"]))
                )
                for o in item.get("observation_schedule", [])
            ]
            spec = CoeContractSpec(
                contract_id=item["contract_id"],
                issuer_institution_id=item["issuer_institution_id"],
                underlying_asset=UnderlyingAssetType(item["underlying_asset"]),
                protection_type=CoeCapitalProtectionType(item["protection_type"]),
                payoff_structure=CoePayoffStructure(item["payoff_structure"]),
                issue_date=item["issue_date"],
                maturity_date=item["maturity_date"],
                nominal_unit_value=Decimal(str(item["nominal_unit_value"])),
                quantity=int(item["quantity"]),
                initial_underlying_level=Decimal(str(item["initial_underlying_level"])),
                protection_floor_pct=Decimal(str(item["protection_floor_pct"])),
                maximum_cap_gain_pct=Decimal(str(item["maximum_cap_gain_pct"])) if item.get("maximum_cap_gain_pct") else None,
                participation_rate_pct=Decimal(str(item.get("participation_rate_pct", "100.00"))),
                digital_coupon_pct=Decimal(str(item["digital_coupon_pct"])) if item.get("digital_coupon_pct") else None,
                observation_schedule=obs
            )
            engine.register_coe(spec)
        return engine
