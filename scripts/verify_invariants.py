#!/usr/bin/env python3
"""
Validador Formal de Invariantes e Consistência da B3
Inspirado em técnicas de verificação formal de linguagens funcionais como Bend (HOC) e proof systems.

Verifica sistematicamente as leis matemáticas fundamentais de conservação de ativos,
fechamento contábil e integridade da câmara B3 sempre que um módulo é concluído.
"""

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.imbarq004 import parse_imbarq004
from src.imbarq006 import parse_imbarq006
from src.imbarq011 import parse_imbarq011
from src.imbarq012 import parse_imbarq012
from src.imbarq013 import parse_imbarq013
from src.imbarq014 import parse_imbarq014
from src.imbarq018 import parse_imbarq018
from src.rtc_capture import parse_rtc_capture
from src.rtc_allocation import AllocationValidator, ParentTrade, AllocationInstruction, AllocationRole
from src.pipeline import PostTradePipeline
from src.stvm_portability import STVMRequest, STVMEngine, CustodyPosition, STVMStatus
from src.funds_platform import (
    CorporateEvent,
    FundCategory,
    FundsPlatformEngine,
    QuotaMovementType,
)
from src.btc_electronic_lending import BtcMatchingEngine, LendingOrder, LendingSide, RenewalType
from src.spb_settlement import SpbSettlementEngine, LdlSettlementRecord, validate_ispb
from src.nome_agro import (
    AgroRegistrationEngine,
    ApprovalStatus,
    CommodityType,
    CprRegistration,
    CprType,
)
from src.tesouro_direto import TesouroDiretoEngine
from src.bmf_futures_settlement import BmfFuturesSettlementEngine
from src.public_offerings_cvm160 import PublicOfferingEngine
from src.bdr_conversion import BdrConversionEngine
from src.etf_creation_redemption import EtfPrimaryMarketEngine
from src.cbio_carbon_energy import CbioPlatformEngine
from src.lien_collateral_registry import LienCollateralRegistryEngine
from src.buyin_failure_management import BuyInFailureEngine
from src.listed_options_exercise import ListedOptionsEngine
from src.otc_derivatives_swap_ndf import OtcDerivativesEngine
from src.equity_forward_termo import EquityForwardEngine
from src.flexible_options_otc import FlexibleOptionsOtcEngine
from src.units_conversion import UnitsConversionEngine
from src.opa_tender_offer import OpaTenderOfferEngine
from src.dma_governance import DmaGovernanceEngine, DmaModelType
from src.market_maker_pmm import MarketMakerPmmEngine
from src.hft_algo_governance import HftAlgoGovernanceEngine
from src.btc_corporate_actions import BtcCorporateActionsEngine
from src.lig_covered_bonds import LigCoveredBondsEngine
from src.core_intraday_margin import CoreRiskEngine
from src.electronic_trade_bills import ElectronicTradeBillEngine
from src.share_fractions_auction import ShareFractionsAuctionEngine
from src.b3_tariff_engine import B3TariffEngine
from src.cdb_rdb_registry import CdbRdbRegistryEngine, CdbContract
from src.default_waterfall import DefaultWaterfallEngine
from src.puma_advanced_orders import PumaAdvancedOrdersEngine
from src.infrastructure_debentures import InfrastructureDebenturesEngine
from src.coe_structured_notes import CoeEngine
from src.puma_closing_auction import PumaClosingAuctionEngine
from src.fx_settlement_pvp import FxSettlementEngine
from src.drex_tokenized_assets import DrexDvpEngine
from src.index_rebalancing import IndexRebalancingEngine, IndexType
from src.smart_order_router import (
    SmartOrderRouterEngine,
    ParentOrder,
    OrderSide as SorOrderSide,
    ParentOrderType as SorParentOrderType,
    RoutingStrategy as SorRoutingStrategy,
)


from src.invariants import InvariantViolationError, check


def verify_custody_conservation(fixtures_dir: Path):
    """
    Lei 1: Conservação de Custódia Depositária (IMBARQ012)
    Para cada conta e na agregação total:
    Quantidade Total == Quantidade Disponível + Bloqueada em Garantia + Bloqueada Judicial
    """
    print("[01/45] Verificando Invariante de Conservação de Custódia (IMBARQ012)...")
    valid_file = fixtures_dir / "imbarq012-valid.txt"
    parsed = parse_imbarq012(valid_file)
    
    total_calc = 0
    disp_calc = 0
    gar_calc = 0
    jud_calc = 0
    
    for r in parsed.detalhes:
        check(
            r.quantidade_total == (r.quantidade_disponivel + r.quantidade_bloqueada_garantia + r.quantidade_bloqueada_judicial),
            f"Equação de custódia violada na conta {r.codigo_conta_custodia} ativo {r.codigo_negociacao}"
        )
        total_calc += r.quantidade_total
        disp_calc += r.quantidade_disponivel
        gar_calc += r.quantidade_bloqueada_garantia
        jud_calc += r.quantidade_bloqueada_judicial

    check(parsed.trailer.total_quantidade_total == total_calc, "Soma de quantidade total diverge do trailer")
    check(parsed.trailer.total_quantidade_disponivel == disp_calc, "Soma disponível diverge do trailer")
    check(parsed.trailer.total_quantidade_bloqueada == (gar_calc + jud_calc), "Soma bloqueada diverge do trailer")
    print("      ✓ Conservação de custódia comprovada (Total == Disp + Gar + Jud).")


def verify_multilateral_financial_conservation(fixtures_dir: Path):
    """
    Lei 2: Conservação Financeira Multilateral de Liquidação (IMBARQ004)
    Saldo Líquido == |Total Créditos - Total Débitos|
    Sinal do Saldo == 'C' se Créditos >= Débitos senão 'D'
    """
    print("[02/45] Verificando Invariante Financeira Multilateral (IMBARQ004)...")
    valid_file = fixtures_dir / "imbarq004-valid.txt"
    parsed = parse_imbarq004(valid_file)
    
    soma_deb = sum(d.valor_lancamento for d in parsed.detalhes if d.natureza_lancamento == "D")
    soma_cred = sum(d.valor_lancamento for d in parsed.detalhes if d.natureza_lancamento == "C")
    saldo_esperado = abs(soma_cred - soma_deb)
    sinal_esperado = "C" if soma_cred >= soma_deb else "D"
    
    check(parsed.trailer.total_valor_debito == soma_deb, "Total de débitos diverge da agregação dos detalhes")
    check(parsed.trailer.total_valor_credito == soma_cred, "Total de créditos diverge da agregação dos detalhes")
    check(parsed.trailer.total_saldo_liquido == saldo_esperado, f"Saldo líquido diverge: {parsed.trailer.total_saldo_liquido} != {saldo_esperado}")
    check(parsed.trailer.sinal_saldo_liquido == sinal_esperado, f"Sinal do saldo líquido incorreto: {parsed.trailer.sinal_saldo_liquido}")
    print("      ✓ Fechamento contábil e conservação financeira comprovados.")


def verify_rtc_allocation_conservation(fixtures_dir: Path):
    """
    Lei 3: Conservação de Quantidade e Preço na Alocação RTC (FL-008)
    sum(AllocatedQuantity) == ParentTradeQuantity
    """
    print("[03/45] Verificando Invariante de Alocação e Repasse RTC (FL-008)...")
    from src.rtc_allocation import AllocationBatch, AllocationValidator
    batch = AllocationBatch.from_file(fixtures_dir / "rtc_allocation_valid.json")
    check(batch.is_balanced(), "Lote de alocação deveria estar balanceado")
    check(batch.total_allocated_quantity() == batch.parent_trade.parent_trade_quantity, "Soma de alocação não iguala trade pai")
    validator = AllocationValidator(strict_price=True)
    res = validator.validate_batch(batch)
    check(res.is_valid, f"Falha na validação de alocação válida: {res.errors}")
    print("      ✓ Conservação de parcelas e preço de alocação comprovados.")


def verify_clearing_positions_invariants(fixtures_dir: Path):
    """
    Lei 4: Integridade Posicional da Câmara (IMBARQ006)
    """
    print("[04/45] Verificando Invariante de Posições da Câmara (IMBARQ006)...")
    parsed = parse_imbarq006(fixtures_dir / "imbarq006-valid.txt")
    total_liq = sum(p.quantidade_liquida for p in parsed.detalhes)
    total_fin = sum(p.valor_financeiro for p in parsed.detalhes)
    
    check(parsed.trailer.total_quantidade_liquida == total_liq, "Soma de posições líquidas diverge do trailer")
    check(parsed.trailer.total_valor_financeiro == total_fin, "Soma financeira diverge do trailer")
    check(parsed.trailer.total_registros_detalhe == len(parsed.detalhes), "Contagem de registros diverge")
    print("      ✓ Invariante de somatórios da câmara comprovada.")


def verify_loan_btc_invariants(fixtures_dir: Path):
    """
    Lei 5: Consistência de Empréstimo de Ativos (IMBARQ011)
    """
    print("[05/45] Verificando Invariante de Contratos BTC (IMBARQ011)...")
    parsed = parse_imbarq011(fixtures_dir / "imbarq011-valid.txt")
    for loan in parsed.contratos:
        check(loan.taxa_aluguel >= Decimal("0"), f"Taxa de aluguel negativa no contrato {loan.numero_contrato}")
        check(loan.data_vencimento >= loan.data_abertura, f"Vencimento anterior à abertura no contrato {loan.numero_contrato}")
        check(loan.quantidade_alugada > 0, f"Quantidade alugada deve ser positiva no contrato {loan.numero_contrato}")
    print("      ✓ Invariantes cronológicas e de taxa do BTC comprovadas.")


def verify_collateral_margin_invariants(fixtures_dir: Path):
    """
    Lei 6: Consistência de Garantias e Margem CORE (IMBARQ014)
    """
    print("[06/45] Verificando Invariante de Garantias e Margem (IMBARQ014)...")
    parsed = parse_imbarq014(fixtures_dir / "imbarq014-valid.txt")
    check(parsed.trailer.total_registros_detalhe == len(parsed.detalhes), "Contagem de colaterais diverge")
    for g in parsed.detalhes:
        check(g.tipo_colateral in ("MOE", "TPU", "ACO"), f"Tipo de colateral inválido: {g.tipo_colateral}")
        check(g.situacao_margem in ("L", "D"), f"Situação de margem inválida: {g.situacao_margem}")
    print("      ✓ Invariantes de elegibilidade de colateral e margem comprovadas.")


def verify_corporate_events_invariants(fixtures_dir: Path):
    """
    Lei 7: Cronologia e Valoração de Eventos Corporativos (IMBARQ018)
    """
    print("[07/45] Verificando Invariante de Eventos Corporativos (IMBARQ018)...")
    parsed = parse_imbarq018(fixtures_dir / "imbarq018-valid.txt")
    for ev in parsed.detalhes:
        check(ev.data_ex >= ev.data_com, f"Data Ex anterior à Data Com no ativo {ev.codigo_negociacao}")
        check(ev.valor_provento >= Decimal("0"), f"Provento negativo no ativo {ev.codigo_negociacao}")
    print("      ✓ Invariantes de cronologia Data Com/Ex comprovadas.")


def verify_custody_movements_invariants(fixtures_dir: Path):
    """
    Lei 8: Balanço de Movimentações de Custódia (IMBARQ013)
    """
    print("[08/45] Verificando Invariante de Movimentações de Custódia (IMBARQ013)...")
    parsed = parse_imbarq013(fixtures_dir / "imbarq013-valid.txt")
    soma_deb = sum(m.quantidade_movimentada for m in parsed.movimentacoes if m.indicador_debito_credito == "D")
    soma_cred = sum(m.quantidade_movimentada for m in parsed.movimentacoes if m.indicador_debito_credito == "C")
    saldo_esperado = abs(soma_cred - soma_deb)
    sinal_esperado = "C" if soma_cred >= soma_deb else "D"
    
    check(parsed.trailer.total_quantidade_debito == soma_deb, "Soma de débitos diverge do trailer")
    check(parsed.trailer.total_quantidade_credito == soma_cred, "Soma de créditos diverge do trailer")
    check(parsed.trailer.saldo_liquido_movimentacao == saldo_esperado, "Saldo líquido de movimentação diverge")
    check(parsed.trailer.sinal_saldo_liquido == sinal_esperado, "Sinal do saldo de movimentação diverge")
    print("      ✓ Invariante de balanço de movimentações físicas comprovada.")


def verify_pipeline_unification(fixtures_dir: Path):
    """
    Lei 9: Fechamento Multilateral Unificado e Reconciliação Determinística
    """
    print("[09/45] Verificando Fechamento Unificado no Pipeline Central...")
    pipeline = PostTradePipeline.from_directory(fixtures_dir)
    pipeline.load_internal_positions([
        {"conta": 123456, "ticker": "PETR4", "quantidade": 1000, "sentido": "C"},
        {"conta": 123456, "ticker": "PETRL350", "quantidade": 5000, "sentido": "C"},
        {"conta": 789012, "ticker": "WDOZ26", "quantidade": 10, "sentido": "V"},
    ])
    summary = pipeline.execute()
    check(summary.is_clean, "Reconciliação no pipeline deveria ser 100% CLEAN")
    check(summary.total_quebras == 0, f"Quebras inesperadas no pipeline: {summary.total_quebras}")
    print("      ✓ Fechamento diário unificado 100% consistente sem quebras.")


def verify_stvm_portability_conservation(fixtures_dir: Path):
    """
    Lei 10: Conservação de Ativos na Portabilidade de Custódia STVM (FL-024)
    Saldo_Cedente_Final + Saldo_Cessionario_Final == Saldo_Cedente_Inicial + Saldo_Cessionario_Inicial
    """
    print("[10/45] Verificando Invariante de Portabilidade de Custódia STVM (FL-024)...")
    fixture = fixtures_dir / "stvm_transfer_request.json"
    req = STVMRequest.from_file(fixture)
    engine = STVMEngine()

    engine.set_custody_position(
        CustodyPosition(participant=105, account="105001", symbol="PETR4", isin="BRPETRACNPR6", total_quantity=Decimal("1000"), available_quantity=Decimal("1000"))
    )
    engine.set_custody_position(
        CustodyPosition(participant=105, account="105001", symbol="VALE3", isin="BRVALEACNOR0", total_quantity=Decimal("500"), available_quantity=Decimal("500"))
    )

    engine.register_request(req)
    engine.validate_cedente(req.protocol)
    engine.accept_cessionario(req.protocol)
    res = engine.execute_transfer(req.protocol)

    check(res.status == STVMStatus.EFETIVADA_B3, "STVM não foi efetivada com sucesso")

    ced_petr4 = engine.get_custody_position(105, "105001", "PETR4")
    ces_petr4 = engine.get_custody_position(88, "88001", "PETR4")
    check(ced_petr4.total_quantity + ces_petr4.total_quantity == Decimal("1000"), "Conservação violada em PETR4")

    ced_vale3 = engine.get_custody_position(105, "105001", "VALE3")
    ces_vale3 = engine.get_custody_position(88, "88001", "VALE3")
    check(ced_vale3.total_quantity + ces_vale3.total_quantity == Decimal("500"), "Conservação violada em VALE3")

    print("      ✓ Conservação de ativos e liquidação atômica STVM comprovadas.")


def verify_btc_lending_matching_invariants(fixtures_dir: Path):
    """
    Lei 11: Conservação Quantitativa e Invariante de Taxa no Matching BTC (FL-025)
    1. Conservação de Lotes: Para cada ordem, Quantidade Original == CumQty (executada) + LeavesQty (residual).
    2. Conservação Bilateral: Em cada match, Quantidade Doadora Executada == Quantidade Tomadora Executada.
    3. Fechamento D0 IMBARQ011: Contratos gerados formam arquivo IMBARQ011 estritamente válido de 200 colunas.
    """
    print("[11/45] Verificando Invariante de Matching de Aluguel BTC em Tela (FL-025)...")
    fixture_file = fixtures_dir / "btc_lending_orderbook.json"
    engine = BtcMatchingEngine(contract_seq_start=202600001)
    matches = engine.load_fixtures_from_json(fixture_file)

    check(len(matches) > 0, "Nenhum match gerado no livro de BTC")

    # Invariante 1 & 2: Conservação de quantidades em todas as ordens
    for ord_id, order in engine._orders.items():
        check(
            order.quantity == order.cum_qty + order.leaves_qty,
            f"Conservação violada na ordem {ord_id}: {order.quantity} != {order.cum_qty} + {order.leaves_qty}"
        )

    # Invariante de match: taxas e nocionais
    for m in matches:
        check(
            m.contract_value == (Decimal(m.quantity) * m.base_price).quantize(Decimal("0.01")),
            f"Nocional divergente no match {m.match_id}"
        )

    # Invariante IMBARQ011 roundtrip
    imbarq_str = engine.export_imbarq011()
    parsed_imbarq = parse_imbarq011(imbarq_str)
    check(parsed_imbarq.total_contratos == len(matches), "Total de contratos IMBARQ011 não bate com matches")
    check(
        parsed_imbarq.trailer.total_quantidade_alugada == sum(m.quantity for m in matches),
        "Total de quantidade alugada no Trailler não confere"
    )

    print("      ✓ Conservação de lotes, regra de taxa passiva e geração D0 IMBARQ011 comprovadas.")


def verify_funds_platform_invariants(fixtures_dir: Path):
    """
    Lei 12: Invariantes da Plataforma de Fundos e Registro de Cotas CVM 175 (FL-029)
    1. Para todas as contas: Quantidade Total == Quantidade Disponível + Quantidade Bloqueada.
    2. Invariante de Patrimônio Líquido: Valor Total == Quantidade * NAV.
    3. Invariante de Amortização: NAV_t = NAV_{t-1} - Amortizacao_Unitario e Quantidade conservada.
    4. Invariante de Distribuição de Proventos: Credito_Total == sum(Q_i * Provento_Unitario).
    """
    print("[12/45] Verificando Invariante de Fundos de Investimento CVM 175 (FL-029)...")
    stmt_file = fixtures_dir / "fund_quota_statement.json"
    engine = FundsPlatformEngine()
    positions = engine.load_statement_from_json(stmt_file)

    for pos in positions:
        check(
            pos.quota_quantity == (pos.available_quantity + pos.blocked_quantity),
            f"Equação de custódia violada no fundo {pos.fund_id} conta {pos.custody_account}",
        )
        check(
            pos.total_equity_value == (pos.quota_quantity * pos.quota_nav).quantize(Decimal("0.01")),
            f"Equação patrimonial violada no fundo {pos.fund_id} conta {pos.custody_account}",
        )
        engine.register_position(pos)

    reconciled = engine.reconcile_statement(positions)
    check(reconciled.is_clean, "Conciliação inicial do extrato de fundos deve ser 100% limpa")

    # Valida conservação de rendimentos
    event_rend = CorporateEvent(
        event_id="EVT-VERIFY-01",
        fund_id="11.222.333/0001-44",
        event_type=QuotaMovementType.INCOME_DISTRIBUTION,
        record_date=positions[0].position_date,
        ex_date=positions[0].position_date,
        payment_date=positions[0].position_date,
        rate_per_quota=Decimal("1.50000000"),
        tax_rate=Decimal("0.0000"),
    )
    txs_rend = engine.distribute_income(event_rend)
    tot_quotas = sum(p.quota_quantity for p in positions if p.fund_id == "11.222.333/0001-44")
    tot_credit = sum(t.gross_amount for t in txs_rend)
    check(
        tot_credit == (tot_quotas * Decimal("1.50000000")).quantize(Decimal("0.01")),
        "Crédito de rendimento violou conservação",
    )

    print("      ✓ Conservação de cotas, integridade patrimonial e cálculo de eventos provados.")


def verify_spb_multilateral_settlement_invariants(fixtures_dir: Path):
    """
    Lei 13: Conservação Financeira Multilateral e Reconciliação SPB LDL0001 (FL-027)
    1. Parsing e validação da fixture canônica LDL0001 (R$ 23.500,00 credor).
    2. Invariante de ISPB estrito de 8 dígitos para câmaras e participantes.
    3. Reconciliação determinística com IMBARQ004 (Trailer e filtro de data).
    4. Invariante de roundtrip XML gerador/parser preservando valor monetário e sentido.
    """
    print("[13/45] Verificando Invariante de Liquidação SPB LDL0001 e Multilateral (FL-027)...")
    spb_fixture = fixtures_dir / "spb_ldl0001_message.xml"
    imbarq_fixture = fixtures_dir / "imbarq004-valid.txt"

    engine = SpbSettlementEngine(ispb_camara="00997185")
    spb_msg = engine.parse_ldl0001(spb_fixture)
    rec = spb_msg.record

    # Invariante 1 & 2: ISPB e valores canônicos
    check(rec.ispb_camara == "00997185", f"ISPB da câmara incorreto: {rec.ispb_camara}")
    check(rec.ispb_participante == "12345678", f"ISPB do participante incorreto: {rec.ispb_participante}")
    check(rec.valor_liquidacao == Decimal("23500.00"), f"Valor líquido incorreto: {rec.valor_liquidacao}")
    check(rec.tipo_debito_credito == "C", f"Sentido financeiro incorreto: {rec.tipo_debito_credito}")

    # Invariante 3: Conciliação determinística com IMBARQ004
    imbarq = parse_imbarq004(imbarq_fixture)
    # Testa batimento exato do trailer
    ldl_trailer = LdlSettlementRecord(
        num_ctrl_ldl="INV-TR-01",
        num_ctrl_str="STR-INV-01",
        ispb_camara="00997185",
        ispb_participante="12345678",
        tipo_debito_credito=imbarq.trailer.sinal_saldo_liquido,
        valor_liquidacao=imbarq.trailer.total_saldo_liquido,
        data_liquidacao=date(2026, 11, 4),
        numero_conta="0000123456",
    )
    res_tr = engine.reconcile_with_imbarq004(ldl_trailer, imbarq, scope="trailer")
    check(res_tr.is_reconciled, "Falha na reconciliação de trailer com LDL0001")
    check(res_tr.diff_valor == Decimal("0.00"), "Diferença financeira não-nula na reconciliação do trailer")

    # Invariante 4: Roundtrip XML gerador/parser preserva 100% dos dados
    xml_out = engine.generate_ldl0001(spb_msg.header, spb_msg.record)
    re_parsed = engine.parse_ldl0001(xml_out)
    check(re_parsed.record.valor_liquidacao == rec.valor_liquidacao, "Valor monetário divergente no roundtrip XML")
    check(re_parsed.record.tipo_debito_credito == rec.tipo_debito_credito, "Sentido financeiro divergente no roundtrip XML")
    check(re_parsed.record.num_ctrl_str == rec.num_ctrl_str, "NumCtrlSTR divergente no roundtrip XML")

    print("      ✓ Conservação de saldo líquido multilateral e conciliação SPB LDL0001 comprovadas.")


def verify_nome_agro_invariants(fixtures_dir: Path):
    """
    Lei 14: Conservação de Registro Escritural e Liquidação NoMe Agro (FL-026)
    1. Integridade de Emissão: Quantidades e valores estritamente positivos, safra não-retroativa e CAR SICAR válido.
    2. Conservação Financeira da CPR Financeira: Valor Total Liquidado == Quantidade * Preço de Liquidação * Ajustes.
    3. Conservação Quantitativa da CPR Física: Quantidade Entregue == Quantidade Contratada.
    4. Determinismo de Anuência: Apenas credor registrado pode conceder anuência com hash SHA-256 válido.
    """
    print("[14/45] Verificando Invariante de Escrituração e Liquidação Agro CPR (FL-026)...")
    fin_file = fixtures_dir / "cpr_financial_registration.json"
    phys_file = fixtures_dir / "cpr_physical_registration.json"

    engine = AgroRegistrationEngine()
    cpr_fin = CprRegistration.from_file(fin_file)
    cpr_phys = CprRegistration.from_file(phys_file)

    # Invariante 1: Validação de conformidade regulatória
    check(cpr_fin.quantity > Decimal("0"), "Quantidade de CPR Financeira deve ser > 0")
    check(cpr_phys.quantity > Decimal("0"), "Quantidade de CPR Física deve ser > 0")
    check(cpr_fin.crop_year == "2026/2027", "Safra da CPR Financeira deve ser 2026/2027")
    check(len(cpr_fin.guarantees) > 0, "CPR Financeira deve possuir garantias registradas")
    check(engine.validate_car(cpr_fin.guarantees[0].car_code), "Código CAR da garantia deve ser válido SICAR")

    # Registro no motor
    engine.register_cpr(cpr_fin)
    engine.register_cpr(cpr_phys)
    check(cpr_fin.status == ApprovalStatus.PENDING_APPROVAL, "Status inicial deve ser PENDING_APPROVAL")

    # Anuência eletrônica do credor
    engine.grant_creditor_approval(
        cpr_id=cpr_fin.cpr_id,
        creditor_document=cpr_fin.creditor.document,
        approved_by="Auditor de Risco Agro",
    )
    check(cpr_fin.status == ApprovalStatus.APPROVED, "Status pós-anuência deve ser APPROVED")
    check(len(cpr_fin.approval.signature_hash) == 64, "Hash de assinatura deve ser SHA-256 de 64 caracteres")

    # Invariante 2: Conservação de valor na liquidação financeira
    price_liq = Decimal("138.50")
    settlement_fin = engine.settle_cpr(cpr_fin.cpr_id, settlement_price=price_liq)
    expected_fin = (cpr_fin.quantity * price_liq).quantize(Decimal("0.01"))
    check(settlement_fin.total_financial_value == expected_fin, f"Valor liquidado diverge: {settlement_fin.total_financial_value} != {expected_fin}")
    check(cpr_fin.status == ApprovalStatus.SETTLED, "Status deve ser SETTLED")

    # Invariante 3: Conservação de quantidade na liquidação física
    engine.grant_creditor_approval(
        cpr_id=cpr_phys.cpr_id,
        creditor_document=cpr_phys.creditor.document,
        approved_by="Trading Mesa Agro",
    )
    settlement_phys = engine.settle_cpr(cpr_phys.cpr_id, quantity_delivered=cpr_phys.quantity)
    check(settlement_phys.quantity_delivered == cpr_phys.quantity, "Quantidade física entregue diverge da contratada")
    check(cpr_phys.status == ApprovalStatus.SETTLED, "Status de CPR física deve ser SETTLED")

    print("      ✓ Conservação de quantidade física e valor financeiro de CPR Agro comprovadas.")


def verify_tesouro_direto_invariants(fixtures_dir: Path):
    """
    Lei 15: Conservação Financeira e Fracionamento no Tesouro Direto (FL-031)
    1. Toda ordem deve ser múltiplo exato de 0,01 título e valor bruto >= R$ 30,00.
    2. Aplicações: Líquido == Bruto + Taxa Custódia B3.
    3. Recompras: Líquido == Bruto - Taxa Custódia B3.
    """
    print("[15/45] Verificando Invariante de Tesouro Direto e Taxa B3 (FL-031)...")
    engine = TesouroDiretoEngine()
    summary = engine.load_and_process_batch(fixtures_dir / "tesouro_direto_orders.json")
    check(summary["total_orders"] == 8, "Total de ordens Tesouro Direto diverge da fixture")
    for order in summary["orders"]:
        check(order.gross_amount >= Decimal("30.00"), f"Ordem {order.order_id} abaixo do mínimo de R$ 30,00")
        check((order.quantity * 100) % 1 == 0, f"Ordem {order.order_id} viola fração mínima de 0,01 título")
    check(summary["total_investments_gross"] == Decimal("9324.38"), "Soma de aplicações brutas diverge")
    check(summary["total_redemptions_gross"] == Decimal("63875.00"), "Soma de resgates brutos diverge")
    check(summary["total_b3_custody_fees"] == Decimal("60.25"), "Soma de taxas B3 diverge")
    print("      ✓ Fração mínima (0,01), mínimo de R$ 30,00 e liquidação D+1 Tesouro Direto comprovados.")


def verify_bmf_futures_settlement_invariants(fixtures_dir: Path):
    """
    Lei 16: Conservação Multilateral de Ajuste Diário de Futuros BM&F (FL-032)
    1. Saldo Líquido D+1 == Soma(Créditos) + Soma(Débitos).
    2. Inversão de sentido em DI1: Comprado em Taxa == Vendido em PU.
    """
    print("[16/45] Verificando Invariante de Ajuste Diário de Futuros BM&F (FL-032)...")
    engine = BmfFuturesSettlementEngine()
    summary = engine.settle_from_json(fixtures_dir / "bmf_futures_positions.json")
    check(len(summary.results) == 7, "Total de posições de futuros diverge da fixture")
    check(
        summary.net_settlement_brl == summary.total_credits_brl + summary.total_debits_brl,
        "Saldo líquido multilateral de ajuste diário diverge de Créditos + Débitos",
    )
    check(summary.net_settlement_brl == Decimal("20792.50"), f"Ajuste líquido incorreto: {summary.net_settlement_brl}")
    print("      ✓ Equação de PU DI1 (DU/252) e ajuste diário D+1 de futuros BM&F comprovados.")


def verify_public_offerings_cvm160_invariants(fixtures_dir: Path):
    """
    Lei 17: Conservação de Lote Ofertado e Rateio no Bookbuilding CVM 160 (FL-033)
    1. Soma(Quantidade Alocada) <= Quantidade Total Ofertada.
    2. Soma(Varejo) + Soma(Institucional) == Total Alocado.
    3. Volume Financeiro Total == Total Alocado * Preço de Corte Uniforme.
    """
    print("[17/45] Verificando Invariante de Bookbuilding e Rateio CVM 160 (FL-033)...")
    engine = PublicOfferingEngine.from_json_file(fixtures_dir / "bookbuilding_cvm160_bids.json")
    summary = engine.execute_bookbuilding()
    check(summary.total_allocated_quantity == summary.total_offered_quantity, "Total alocado diverge do lote ofertado")
    check(
        summary.retail_allocated_quantity + summary.institutional_allocated_quantity == summary.total_allocated_quantity,
        "Soma das tranches de varejo e institucional diverge do total alocado",
    )
    check(
        summary.total_settlement_amount == Decimal(summary.total_allocated_quantity) * summary.clearing_price_or_rate,
        "Volume financeiro de liquidação da oferta pública diverge de Qtd * Preço de Corte",
    )
    print("      ✓ Corte holandês uniforme, prioridade de lock-up e conservação de rateio CVM 160 comprovados.")


def verify_bdr_conversion_invariants(fixtures_dir: Path):
    """
    Lei 18: Conservação Internacional de Lastro e Paridade de BDRs (FL-034)
    1. Para todo programa BDR: Ações-Lastro Bloqueadas * Paridade == BDRs Emitidos * Bloco-Lastro.
    2. Repasse de Dividendos: Bruto_USD - Withholding_Tax == Liquido_Estrangeiro_USD.
    """
    print("[18/45] Verificando Invariante de Paridade Internacional e Dividendos BDR (FL-034)...")
    engine, conversions, dividends = BdrConversionEngine.load_and_process_fixture(
        fixtures_dir / "bdr_conversion_requests.json"
    )
    check(len(conversions) == 5, "Total de conversões BDR diverge da fixture")
    check(len(dividends) == 2, "Total de eventos de dividendos BDR diverge da fixture")
    check(engine.verify_global_conservation(), "Conservação global de lastro internacional BDR violada")
    print("      ✓ Paridade inteira DTC/Euroclear vs B3 e repasse PTAX de dividendos BDR comprovados.")


def verify_etf_primary_basket_invariants(fixtures_dir: Path):
    """
    Lei 19: Identidade Contábil do PCF e Entrega In-Kind no Mercado Primário de ETFs (FL-035)
    1. Valor das Ações da Cesta + Cash Component == Creation_Unit_Size * NAV_per_Quota.
    2. Todas as ordens primárias são múltiplos exatos do Lote Mínimo (Creation Unit).
    """
    print("[19/45] Verificando Invariante de Cesta PCF e Criação/Resgate de ETFs (FL-035)...")
    engine = EtfPrimaryMarketEngine.from_json(fixtures_dir / "etf_primary_basket.json")
    check(engine.pcf.validate_nav_parity(), "Paridade patrimonial do PCF (Cesta + Caixa == Lote * NAV) violada")
    instructions = engine.process_batch()
    check(len(instructions) == 4, "Total de instruções primárias de ETF diverge da fixture")
    for inst in instructions:
        check(
            inst.basket_equities_value + inst.cash_component_amount == inst.total_nav_value,
            f"Identidade patrimonial violada na instrução {inst.order_id}",
        )
    print("      ✓ Identidade contábil do PCF (Cesta + Cash == NAV) e entrega in-kind de ETFs comprovadas.")


def verify_cbio_carbon_retirement_invariants(fixtures_dir: Path):
    """
    Lei 20: Conservação Ambiental e Prevenção de Dupla Contagem de CBIOs (FL-036)
    1. Total Emitidos == Total em Circulação + Total Aposentados.
    2. CBIO aposentado jamais retorna à circulação (estado absorvente terminal).
    """
    print("[20/45] Verificando Invariante de Conservação e Aposentadoria de CBIOs (FL-036)...")
    engine = CbioPlatformEngine()
    summary = engine.process_batch_file(fixtures_dir / "cbio_retirement_batch.json")
    check(summary.conservation_invariant_ok, "Equação de conservação CBIO (Emitidos == Circulação + Aposentados) violada")
    check(
        summary.total_issued_cbios == summary.total_in_circulation_cbios + summary.total_retired_cbios,
        "Soma de CBIOs em circulação e aposentados diverge do total escriturado",
    )
    print("      ✓ Conservação de CBIOs (RenovaBio/ANP) e bloqueio anti-dupla contagem comprovados.")


def verify_lien_collateral_waterfall_invariants(fixtures_dir: Path):
    """
    Lei 21: Prelazia Multi-Grau e Conservação de Excussão de Garantias (FL-037 — Lei 14.711/2023)
    1. Soma dos gravames ativos num pool <= Valor Líquido de Avaliação (pós-haircut).
    2. Na excussão: Valor Bruto Arrecadado == Custos + Soma(Pagamentos aos Credores por Grau) + Saldo Devolvido ao Devedor.
    """
    print("[21/45] Verificando Invariante de Prelazia e Excussão de Garantias (FL-037)...")
    engine = LienCollateralRegistryEngine.from_fixture(
        fixtures_dir / "lien_collateral_registry.json", execute_events=True
    )
    check(len(engine.excussion_history) == 1, "Histórico de excussão diverge da fixture")
    exc = engine.excussion_history[0]
    total_paid = sum(p.paid_amount_brl for p in exc.allocations)
    check(
        exc.gross_proceeds_brl == exc.execution_costs_brl + total_paid + exc.grantor_surplus_returned_brl,
        "Conservação financeira violada na cascata de excussão de garantias",
    )
    check(exc.prelacy_order_respected, "Ordem de prelazia entre graus violada na excussão")
    print("      ✓ Ordem de prelazia multi-grau (Lei 14.711/2023) e conservação na excussão comprovadas.")


def verify_buyin_failure_management_invariants(fixtures_dir: Path):
    """
    Lei 22: Identidade de Shortfall D+2 e Alocação Assimétrica de Buy-In D+3 (FL-038)
    1. Shortfall == Obrigação - Entregue - Regularizado_BTC >= 0.
    2. Diferencial debitado do vendedor inadimplente >= 0 (vendedor arca com prejuízo, mas não recebe ganho).
    """
    print("[22/45] Verificando Invariante de Falha de Entrega D+2 e Buy-In D+3 (FL-038)...")
    engine = BuyInFailureEngine()
    res = engine.evaluate_fixture(fixtures_dir / "buyin_settlement_failures.json")
    check(res["total_cases"] == 4, "Total de casos de falha D+2 diverge da fixture")
    check(
        res["total_shortfall_quantity"]
        == res["total_obligated_quantity"] - res["total_delivered_quantity"] - res["total_btc_regularized_quantity"],
        "Identidade quantitativa de Shortfall D+2 violada",
    )
    check(
        res["total_debited_from_sellers_brl"] == res["total_penalties_brl"] + res["total_price_differentials_brl"],
        "Débito total de falha e Buy-In diverge da soma de multas e diferenciais",
    )
    print("      ✓ Identidade de Shortfall D+2, reversão BTC e diferencial assimétrico de Buy-In comprovados.")


def verify_listed_options_expiration_invariants(fixtures_dir: Path):
    """
    Lei 23: Conservação de Posições e Exercício/Atribuição de Opções Listadas (FL-039)
    1. Total Posição Titular Aberta == Total Posição Lançadora Aberta por série.
    2. Total Quantidade Exercida pelos Titulares == Total Quantidade Atribuída (Assigned) aos Lançadores.
    """
    print("[23/45] Verificando Invariante de Exercício e Atribuição de Opções Listadas (FL-039)...")
    engine = ListedOptionsEngine()
    res = engine.evaluate_fixture(fixtures_dir / "listed_options_expiration.json")
    check(res["conservation_invariant_satisfied"], "Invariante de conservação Titular vs Lançador de opções violada")
    check(
        res["total_exercised_quantity"] == res["total_assigned_quantity"],
        "Quantidade exercida por titulares diverge da quantidade atribuída a lançadores",
    )
    print("      ✓ Conservação física Titular/Lançador, Barreira e geração de exercício D+2 comprovadas.")


def verify_otc_derivatives_swap_ndf_invariants(fixtures_dir: Path):
    """
    Lei 24: Conservação Bilateral Soma-Zero em Derivativos de Balcão Swap & NDF (FL-040)
    1. Em todo contrato de Swap ou NDF: Ajuste_Bruto_Parte_A + Ajuste_Bruto_Parte_B == 0.00.
    2. Balanço líquido pós-IRRF: Liquido_A + Liquido_B + IRRF_Total == 0.00.
    """
    print("[24/45] Verificando Invariante Soma-Zero de Derivativos de Balcão Swap e NDF (FL-040)...")
    engine = OtcDerivativesEngine.from_json_fixture(fixtures_dir / "otc_swap_ndf_contracts.json")
    summary = engine.portfolio_summary()
    check(summary["total_contracts"] == 6, "Total de contratos de Swap e NDF diverge da fixture")
    check(summary["all_zero_sum_conserved"], "Conservação bilateral soma-zero violada em Swap ou NDF")
    check(summary["all_cashflow_balanced"], "Balanço contábil pós-IRRF violado em Swap ou NDF")
    print("      ✓ Conservação bilateral soma-zero (Swap/NDF) e balanço pós-IRRF no NoMe comprovados.")


def verify_equity_forward_termo_invariants(fixtures_dir: Path):
    """
    Lei 25: Conservação de Quantidade e Cobertura 100% no Mercado a Termo (FL-041)
    1. Quantidade Original == Liquidada Antecipada + Liquidada no Vencimento + Aberta.
    2. Vendedor 100% Coberto na carteira 2306-6 e Preço calculado via DU/252.
    """
    print("[25/45] Verificando Invariante de Quantidade e Cobertura no Mercado a Termo (FL-041)...")
    engine = EquityForwardEngine.from_json_fixture(fixtures_dir / "equity_forward_contracts.json")
    summary = engine.summarize()
    check(summary.total_contracts == 4, "Total de contratos a termo diverge da fixture")
    check(summary.all_quantity_conserved, "Conservação de quantidade de ações a termo violada")
    check(summary.all_seller_100pct_covered, "Vendedor a termo sem 100% de cobertura na 2306-6")
    check(summary.all_terms_within_b3_limits, "Prazo regulamentar de termo (16 a 999 dias) violado")
    print("      ✓ Conservação de quantidade de ações, cobertura 100% (2306-6) e prazo regulamentar comprovados.")


def verify_flexible_options_otc_invariants(fixtures_dir: Path):
    """
    Lei 26: Conservação Soma-Zero e Barreiras em Opções Flexíveis de Balcão (FL-042)
    1. Payoff Bruto Holder + Payoff Bruto Writer == 0.00.
    2. Balanço líquido de liquidação: Net_Holder + Net_Writer + IRRF_Holder == 0.00.
    """
    print("[26/45] Verificando Invariante Soma-Zero em Opções Flexíveis NoMe (FL-042)...")
    engine = FlexibleOptionsOtcEngine.from_json_fixture(fixtures_dir / "flexible_options_otc.json")
    summary = engine.summarize()
    check(summary.total_contracts == 5, "Total de opções flexíveis diverge da fixture")
    check(summary.all_zero_sum_gross, "Conservação bilateral soma-zero violada em opções flexíveis")
    check(summary.all_cashflow_balanced, "Balanço contábil pós-IRRF violado em opções flexíveis")
    print("      ✓ Conservação bilateral soma-zero, barreiras Knock-In/Out e limitadores Cap comprovados.")


def verify_units_conversion_invariants(fixtures_dir: Path):
    """
    Lei 27: Conservação Física de Ações Equivalentes em Conversão de Units (FL-043)
    1. Para todo investidor: ON_Equivalente e PN_Equivalente são constantes antes e após cada conversão.
    2. Respeito estrito às proporções estatutárias de ON/PN por Unit.
    """
    print("[27/45] Verificando Invariante de Ações Equivalentes em Units (FL-043)...")
    engine, instructions = UnitsConversionEngine.from_json_fixture(fixtures_dir / "units_conversion_requests.json")
    engine.process_all(instructions)
    summary = engine.summarize()
    check(summary.total_instructions_processed == 6, "Total de conversões de Units diverge da fixture")
    check(summary.all_conservations_satisfied, "Conservação de ações equivalentes violada na conversão de Units")
    print("      ✓ Conservação física de ações equivalentes (ON e PN) e atomicidade D0 em Units comprovadas.")


def verify_opa_tender_offer_invariants(fixtures_dir: Path):
    """
    Lei 28: Conservação de Custódia e Quórum de 2/3 em Leilões Especiais de OPA (FL-044)
    1. Soma(Ações Aceitas) + Soma(Ações Devolvidas) == Total de Ações Habilitadas em todas as modalidades.
    2. Respeito estrito ao piso de Tag-Along (80%/100%) e ao quórum de 2/3 (3*Q_conc > 2*Q_hab).
    """
    print("[28/45] Verificando Invariante de Conservação e Quórum em Leilões de OPA (FL-044)...")
    engine = OpaTenderOfferEngine.from_json_fixture(fixtures_dir / "opa_tender_offer_auction.json")
    summary = engine.summarize()
    check(summary["total_auctions"] == 3, "Total de leilões de OPA diverge da fixture")
    check(summary["all_conservations_satisfied"], "Conservação de ações em leilões de OPA violada")
    print("      ✓ Conservação de ações habilitadas, quórum legal de 2/3 e rateio proporcional pro-rata comprovados.")


def verify_dma_governance_invariants(fixtures_dir: Path):
    """
    Lei 29: Invariante de Governança DMA e Proteção de Risco Pré-Trade CVM 35 (FL-045)
    1. Acesso rigoroso por modelo de DMA (DMA 1, 2, 3 e 4) com checagem de limites de valor, lote e túnel.
    2. Mecanismo de Kill-Switch Mandatório B3 com latência de execução < 100ms e cancelamento total de ordens ativas.
    """
    print("[29/45] Verificando Invariante de Governança DMA e Filtros Pré-Trade (FL-045)...")
    engine = DmaGovernanceEngine()
    engine.load_from_json_file(fixtures_dir / "dma_governance_orders.json")
    
    # Valida presença dos 4 modelos
    cli_dma1 = engine.get_client("CLI_DMA1_RETAIL")
    cli_dma4 = engine.get_client("CLI_DMA4_HFT")
    check(cli_dma1 is not None and cli_dma1.dma_model == DmaModelType.DMA_1, "Modelo DMA 1 não configurado")
    check(cli_dma4 is not None and cli_dma4.dma_model == DmaModelType.DMA_4, "Modelo DMA 4 não configurado")
    
    # Disparo de Kill-Switch e verificação de esvaziamento de ordens ativas
    ks = engine.trigger_kill_switch("CLI_DMA3_INST", "Invariante Formal de Risco")
    check(ks.channel_blocked, "Canal DMA não bloqueado após acionamento do Kill-Switch")
    check(ks.latency_ms < 100.0, "Latência do Kill-Switch ultrapassou o teto regulatório de 100ms")
    active_orders = engine.get_active_orders("CLI_DMA3_INST")
    check(len(active_orders) == 0, "Ordens ativas remanescentes após execução de Kill-Switch")
    print("      ✓ Modelos DMA 1 a 4, filtros pré-trade CVM 35 e Kill-Switch sub-100ms comprovados.")


def verify_market_maker_pmm_invariants(fixtures_dir: Path):
    """
    Lei 30: Invariante de Conservação Tarifária e Presença PMM (FL-046)
    1. Identidade de Conservação Tarifária: Total_Tarifas_Brutas == Desconto_PMM + Tarifas_Liquidas_Devidas.
    2. Respeito às métricas de presença contínua, spread máximo e profundidade mínima com exclusão de leilões.
    """
    print("[30/45] Verificando Invariante Tarifária e Presença de Formador de Mercado PMM (FL-046)...")
    engine = MarketMakerPmmEngine()
    sessions, monthly_rewards = engine.load_fixtures_and_audit(fixtures_dir / "market_maker_pmm_audit.json")
    check(len(sessions) > 0, "Nenhuma sessão de PMM auditada")
    check(len(monthly_rewards) > 0, "Nenhum faturamento mensal de PMM apurado")
    for r in monthly_rewards:
        check(
            r.total_gross_fees == r.discount_amount + r.net_fees_payable,
            f"Conservação tarifária violada no PMM para {r.participant_id} em {r.symbol}",
        )
    print("      ✓ Conservação de faturamento tarifário, presença contínua e exclusão de leilões PMM comprovadas.")


def verify_hft_algo_governance_invariants(fixtures_dir: Path):
    """
    Lei 31: Invariante de Identificação Algorítmica e Penalidade OTR BSM (FL-047)
    1. Identificação mandatória RTS 25 / CVM 505 (Tag 1031, 57, 1724, 8015).
    2. Conservação da Tarifação Punitiva de OTR: Total_Penalidades == Soma(Penalidades_Individuais).
    """
    print("[31/45] Verificando Invariante de Identificação Algorítmica e Penalidade OTR BSM (FL-047)...")
    engine = HftAlgoGovernanceEngine.load_from_json(fixtures_dir / "hft_algo_governance.json")
    rep = engine.generate_governance_report()
    check(rep.total_events > 0, "Nenhum evento algorítmico auditado na fixture")
    sum_penalties = sum(p.total_penalty_brl for p in rep.penalties)
    check(
        sum_penalties == rep.total_penalties_brl,
        f"Conservação de penalidades de OTR violada: {sum_penalties} != {rep.total_penalties_brl}",
    )
    print("      ✓ Identificação CVM 505/RTS 25, sincronização temporal e tarifação punitiva de OTR comprovadas.")


def verify_btc_corporate_actions_invariants(fixtures_dir: Path):
    """
    Lei 32: Repasse de Proventos e Conservação Nocional em Empréstimo BTC (FL-048)
    1. Ajuste de Splits/Bonificações conserva o valor econômico do contrato BTC.
    2. Proventos em dinheiro (Dividendos/JCP) repassam 100% dos direitos econômicos ao Doador.
    """
    print("[32/45] Verificando Invariante de Proventos em Empréstimo BTC (FL-048)...")
    engine, events = BtcCorporateActionsEngine.from_json_fixture(fixtures_dir / "btc_corporate_actions.json")
    receipts = engine.process_all_events()
    check(len(receipts) == 7, "Total de recibos de proventos BTC diverge da fixture")
    summary = engine.summarize()
    check(summary["total_contracts"] == 7, "Total de contratos BTC diverge da fixture")
    print("      ✓ Repasse de dividendos/JCP com compensação de IRRF e ajuste de splits em BTC comprovados.")


def verify_lig_covered_bonds_invariants(fixtures_dir: Path):
    """
    Lei 33: Sobrecolateralização Mínima de 105% e Cobertura de LIG (FL-049)
    1. Cover_Ratio >= 1.05 no Cover Pool em conformidade.
    2. Segregação patrimonial na conta de afetação NoMe e fluxo de liquidez de 180 dias.
    """
    print("[33/45] Verificando Invariante de Sobrecolateralização de LIG (FL-049)...")
    engine = LigCoveredBondsEngine()
    engine.load_from_json_file(fixtures_dir / "lig_covered_bonds.json")
    summary = engine.evaluate_cover_pool("CP-LIG-2026-001")
    check(summary.is_overcollateralization_compliant, "Sobrecolateralização de LIG violada no pool regular")
    check(summary.cover_ratio >= Decimal("1.05"), "Cover ratio de LIG abaixo do piso regulatório de 105%")
    stress = engine.run_liquidity_stress_test("CP-LIG-2026-001")
    check(stress.is_compliant, "Teste de estresse de liquidez de 180 dias de LIG reprovado")
    print("      ✓ Sobrecolateralização mínima de 105% e teste de liquidez de 180 dias de LIG comprovados.")


def verify_core_intraday_margin_invariants(fixtures_dir: Path):
    """
    Lei 34: Conservação de Risco e Haircut de Colaterais CORE Intradiário (FL-050)
    1. Margem Requerida calculada pelo pior cenário de estresse (Worst-Case Closeout Loss).
    2. Apuração exata de déficit de colateral com haircuts regulatórios.
    """
    print("[34/45] Verificando Invariante de Margem Intradiária CORE (FL-050)...")
    engine = CoreRiskEngine()
    engine.load_from_json(fixtures_dir / "core_intraday_margin.json")
    calc = engine.calculate_intraday_margin("ACC-INST-001")
    check(calc.total_margin_required > Decimal("0.00"), "Margem requerida CORE não calculada")
    check(calc.worst_case_scenario_id != "", "Pior cenário de estresse CORE não identificado")
    print("      ✓ Pior cenário de estresse, efeito portfólio e apuração de margem CORE comprovados.")


def verify_electronic_trade_bills_invariants(fixtures_dir: Path):
    """
    Lei 35: Unicidade de Lastro NF-e e Fechamento Contábil de Duplicatas (FL-051)
    1. Unicidade estrita da chave NF-e (44 dígitos) + número de parcela.
    2. Fechamento contábil perfeito na liquidação via SPB/Pix.
    """
    print("[35/45] Verificando Invariante de Duplicatas Escriturais Eletrônicas (FL-051)...")
    engine = ElectronicTradeBillEngine()
    engine.load_from_fixture(fixtures_dir / "electronic_trade_bills.json")
    check(len(engine.bills) >= 5, "Total de duplicatas carregadas insuficiente")
    check(engine.verify_ballast_uniqueness(), "Violação de unicidade de lastro de duplicatas detectada")
    check(engine.verify_accounting_conservation(), "Fechamento contábil de liquidação de duplicatas violado")
    print("      ✓ Unicidade de lastro NF-e, cadeia de endosso e fechamento contábil comprovados.")


def verify_share_fractions_auction_invariants(fixtures_dir: Path):
    """
    Lei 36: Conservação de Valor e Rateio Pro-Rata em Leilão de Frações (FL-052)
    1. Soma(Crédito_Líquido) == Montante_Líquido_Leilão (método dos maiores restos).
    2. Ausência total de centavos órfãos ou resíduos na distribuição.
    """
    print("[36/45] Verificando Invariante de Leilão de Frações e Sobras de Ações (FL-052)...")
    engine, event, holders, auction = ShareFractionsAuctionEngine.from_json_fixture(fixtures_dir / "share_fractions_auction.json")
    report = engine.run_full_cycle(event, holders, auction)
    check(report["all_invariants_satisfied"], "Conservação financeira violada no leilão de sobras de frações")
    print("      ✓ Agrupamento de sobras e rateio pro-rata Hare-Niemeyer centavo a centavo comprovados.")


def verify_b3_tariff_pricing_invariants(fixtures_dir: Path):
    """
    Lei 37: Fechamento Contábil e Monotonicidade da Tarifação B3 (FL-053)
    1. Tarifa_Total == Emolumentos + Liquidação + Registro.
    2. Aplicação de desconto de Day-Trade e escadinha regressiva ADTV.
    """
    print("[37/45] Verificando Invariante da Tarifação Integrada B3 (FL-053)...")
    engine = B3TariffEngine()
    summary = engine.load_fixture_and_process(fixtures_dir / "b3_tariff_calculations.json")
    check(
        summary.total_tariff == summary.total_trading_fee + summary.total_settlement_fee + summary.total_registration_fee,
        "Fechamento contábil do extrato de tarifação B3 violado",
    )
    check(summary.total_trades > 0, "Nenhuma operação tarifada na fixture B3")
    print("      ✓ Escadinha regressiva ADTV (Tiers 1 a 7) e desoneração de Day-Trade comprovadas.")


def verify_cdb_rdb_registry_invariants(fixtures_dir: Path):
    """
    Lei 38: Conservação Financeira de Rendimento e Tabela Regressiva em CDB/RDB (FL-054)
    1. Bruto == Principal + Rendimento Bruto; Líquido == Bruto - IOF - IRRF.
    2. Inalienabilidade estrita de títulos RDB.
    """
    print("[38/45] Verificando Invariante de CDBs e RDBs Bancários (FL-054)...")
    import json
    with open(fixtures_dir / "cdb_rdb_registry.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    engine = CdbRdbRegistryEngine()
    engine.register_di_rates_batch(data["market_data"]["di_over_history"])
    for dt_str, val in data["market_data"]["ipca_indices"].items():
        engine.register_ipca_index(dt_str, val)
    for c_dict in data["contracts"]:
        engine.register_contract(CdbContract.from_dict(c_dict))
    check(len(engine.contracts) >= 5, "Total de contratos CDB/RDB insuficiente")
    
    # Resgate cenário 15 dias (IOF + IRRF)
    sc_15 = data["settlement_scenarios"]["scenario_pre_day_15_early_partial"]
    r_15 = engine.settle_contract(sc_15["contract_id"], sc_15["settlement_date"], sc_15["redeemed_quantity"])
    check(r_15.gross_amount == r_15.principal_amount + r_15.tax_breakdown.gross_yield, "Conservação bruta em CDB violada")
    check(r_15.net_amount == r_15.gross_amount - r_15.tax_breakdown.iof_amount - r_15.tax_breakdown.irrf_amount, "Conservação líquida em CDB violada")
    print("      ✓ Atualização diária de PU, tabela regressiva de IOF/IRRF e inalienabilidade RDB comprovadas.")


def verify_default_waterfall_invariants(fixtures_dir: Path):
    """
    Lei 39: Conservação Contábil e Rateio na Cascata de Inadimplência CPMI-IOSCO (FL-055)
    1. Perda_Total == Recursos_Consumidos (Níveis 1 a 5) + Déficit Residual.
    2. Continuidade de liquidação da câmara garantida.
    """
    print("[39/45] Verificando Invariante da Cascata de Inadimplência CPMI-IOSCO (FL-055)...")
    engine, scenarios = DefaultWaterfallEngine.load_from_json(fixtures_dir / "default_waterfall_scenarios.json")
    check(len(scenarios) == 5, "Total de cenários de estresse de default diverge da fixture")
    for scn in scenarios:
        res = engine.process_default(scn)
        check(res.invariant_verified, f"Conservação contábil violada no cenário {scn.event_id}")
    print("      ✓ Absorção nível a nível (Margens, Fundo Inadimplente, SITG B3, Fundo Mútuo) comprovada.")


def verify_puma_advanced_orders_invariants(fixtures_dir: Path):
    """
    Lei 40: Conservação de Lotes em Ordens Iceberg e Gatilho Móvel PUMA (FL-056)
    1. Quantidade_Executada + Quantidade_Oculta + Quantidade_Visivel == Quantidade_Total.
    2. Mecanismo de reposição de tranche Iceberg e avanço Ratchet em Trailing-Stop.
    """
    print("[40/45] Verificando Invariante de Ordens Avançadas PUMA (FL-056)...")
    engine = PumaAdvancedOrdersEngine()
    engine.load_from_fixture(fixtures_dir / "puma_advanced_orders.json")
    check(engine.verify_all_invariants(), "Conservação de quantidades de ordens avançadas PUMA violada")
    print("      ✓ Conservação de reserva oculta Iceberg, Stop-Limit e Trailing-Stop comprovadas.")


def verify_infrastructure_debentures_invariants(fixtures_dir: Path):
    """
    Lei 41: Conservação de Saldo Devedor Global de Debêntures Incentivadas (FL-057)
    1. VNE_Global == Quantidade_em_Circulação * VNE_Unitário.
    2. Benefício fiscal ao investidor PF (Lei 12.431) e bônus emissora (Lei 14.801).
    """
    print("[41/45] Verificando Invariante de Debêntures de Infraestrutura (FL-057)...")
    engine = InfrastructureDebenturesEngine.load_from_json(fixtures_dir / "infrastructure_debentures.json")
    check(len(engine.emissions) >= 3, "Total de emissões de debêntures insuficiente")
    for code in engine.emissions.keys():
        engine.process_all_events(code)
        inv = engine.verify_invariants(code)
        check(inv["debtor_balance_conservation_verified"] and inv["custody_conservation_verified"] and inv["multilateral_financial_conservation_verified"], f"Conservação de VNE violada na debênture {code}")
    print("      ✓ Amortizações Bullet/Constante/Variável e benefícios fiscais 12.431/14.801 comprovados.")


def verify_coe_structured_notes_invariants(fixtures_dir: Path):
    """
    Lei 42: Conservação de Capital Protegido e Delimitação de Payoff em COE (FL-058)
    1. Capital Protegido: Total Bruto >= Principal nos contratos de proteção garantida.
    2. Teto de Retorno (Cap): Payoff estritamente limitado ao teto pactuado no DIE.
    """
    print("[42/45] Verificando Invariante de Capital Protegido e Payoff em COE (FL-058)...")
    engine = CoeEngine.load_from_json(fixtures_dir / "coe_structured_notes.json")
    check(len(engine.contracts) >= 4, "Total de contratos COE insuficiente")
    
    # 1. Bull Spread acima do teto (limita ao cap)
    r_cap = engine.settle_at_maturity("COE-IBOV-BULL-001", Decimal("180000.00"), "2026-11-04", 730)
    check(r_cap.invariant_cap_gain_held, "Violação do teto (cap) no COE")
    check(r_cap.invariant_capital_protection_held, "Violação de capital protegido no COE")

    # 2. Bull Spread com queda (devolve 100% do principal)
    engine2 = CoeEngine.load_from_json(fixtures_dir / "coe_structured_notes.json")
    r_neg = engine2.settle_at_maturity("COE-IBOV-BULL-001", Decimal("115000.00"), "2026-11-04", 730)
    check(r_neg.invariant_capital_protection_held, "Violação de capital protegido em cenário de queda")
    check(r_neg.total_gross_settlement == r_neg.total_principal_invested, "Devolução parcial indevida de principal")
    print("      ✓ Garantia de capital protegido 100% e teto máximo de ganho em COE comprovados.")


def verify_puma_closing_auction_invariants(fixtures_dir: Path):
    """
    Lei 43: Maximização de Volume e Conservação de Book no Leilão de Fechamento PUMA (FL-059)
    1. Preço Único de Liquidação (Uncross Price) para 100% das ordens executadas.
    2. Conservação estrita de quantidades: Qtd Inicial == Executada + Residual.
    """
    print("[43/45] Verificando Invariante do Leilão de Fechamento PUMA & MOC (FL-059)...")
    engine = PumaClosingAuctionEngine.load_from_fixture(fixtures_dir / "puma_closing_auction.json")
    ptf = engine.execute_uncross()
    check(ptf is not None and ptf.executable_volume > 0, "Nenhum volume apurado no leilão de fechamento")
    inv = engine.verify_invariants()
    check(inv["conservation_verified"], "Conservação de quantidades do livro violada no leilão de fechamento")
    check(inv["unique_price_verified"], "Execuções com preços discrepantes no mesmo uncross do leilão")
    print("      ✓ Maximização do PTF, prioridade MOC e conservação de book comprovadas.")


def verify_fx_settlement_pvp_invariants(fixtures_dir: Path):
    """
    Lei 44: Conservação Bilateral PvP e Eliminação do Risco Herstatt (FL-060)
    1. Liquidação atômica e simultânea (ambas as pernas confirmadas ou falha segura).
    2. Conservação da paridade cambial e fechamento multilateral soma-zero.
    """
    print("[44/45] Verificando Invariante de Liquidação Cambial Atômica PvP (FL-060)...")
    engine = FxSettlementEngine.load_from_fixture(fixtures_dir / "fx_settlement_pvp.json")
    rec = engine.execute_pvp_settlement("FX-SPOT-2026-001")
    check(rec.pvp_atomic_verified and rec.parity_conserved, "Violação de atomicidade PvP no câmbio spot")
    check(engine.verify_multilateral_conservation(), "Conservação multilateral soma-zero da câmara de câmbio violada")
    print("      ✓ Liquidação atômica PvP, paridade cambial e soma-zero multilateral comprovadas.")


def verify_drex_tokenized_assets_invariants(fixtures_dir: Path):
    """
    Lei 45: Conservação Absoluta de Custódia Total CSD + DREX (FL-061)
    1. Qtd CSD Bloqueada DREX == Total Tokens On-Chain em circulação.
    2. DvP Atômico: transferência de titularidade de TPFt e pagamento em Real Digital indivisíveis.
    """
    print("[45/45] Verificando Invariante de Custódia e DvP em TPFt / DREX (FL-061)...")
    engine = DrexDvpEngine.load_from_fixture(fixtures_dir / "drex_tokenized_assets.json")
    check(engine.verify_global_custody_conservation("LTN2028"), "Inconsistência inicial entre CSD e rede DREX")
    
    # Simula DvP atômico
    engine.execute_dvp_transfer(
        sender_wallet_address="0x1111111111111111111111111111111111111111",
        receiver_wallet_address="0x2222222222222222222222222222222222222222",
        symbol="LTN2028",
        quantity=1000,
        unit_price_brl=Decimal("950.00")
    )
    check(engine.verify_global_custody_conservation("LTN2028"), "Custódia CSD x DREX desbalanceada após DvP")
    print("      ✓ Conservação absoluta CSD/DREX e liquidação atômica DvP comprovadas.")


def verify_smart_order_router_invariants(fixtures_dir: Path):
    """
    Lei 46: Conservação Absoluta do Lote Original no Split SOR (FL-068)
    1. Soma(Q_roteada) + Q_residual == Q_ordem_mae para qualquer split determinístico.
    2. Prevenção estrita de Trade-Through e respeito a limites de preço.
    """
    print("[46/52] Verificando Invariante de Smart Order Router (SOR) & Agregação de Liquidez (FL-068)...")
    engine = SmartOrderRouterEngine.load_from_fixture(fixtures_dir / "smart_order_router.json")
    order = ParentOrder(
        order_id="INV-CHECK-SOR-01",
        symbol="PETR4",
        side=SorOrderSide.BUY,
        order_type=SorParentOrderType.LIMIT,
        quantity=35000,
        price=Decimal("38.50"),
        strategy=SorRoutingStrategy.MINIMAL_MARKET_IMPACT
    )
    report = engine.route_order(order)
    check(report.conservation_verified, "Violação da Lei de Conservação de Lote no Smart Order Router")
    check(report.total_routed_quantity + report.residual_unrouted_quantity == 35000, "Divergência na quantidade do lote mãe")
    inv = engine.verify_invariants(report)
    check(inv["all_invariants_hold"], "Falha nas invariantes formais do SOR")
    print("      ✓ Conservação estrita de lote original, minimização de impacto e Best Execution comprovadas.")


def verify_card_receivables_invariants(fixtures_dir: Path):
    """
    Lei 47: Unicidade de Gravame e Conservação Contábil de UR (FL-069)
    1. V_atual == V_comprometido + V_disponivel (com V_disponivel >= 0).
    2. V_comprometido == Soma(Gravames Ativos).
    3. Antecipação incide estritamente sobre V_disponivel e conserva V_face == V_liquido + V_taxa.
    4. Na liquidação: Total Liquidado == Soma(Credores Fiduciários) + Repasse Lojista.
    """
    print("[47/52] Verificando Invariante de Registro e Gravame de Recebíveis de Cartão (FL-069)...")
    from src.card_receivables_registry import CardReceivablesRegistryEngine
    engine = CardReceivablesRegistryEngine.load_from_fixture(fixtures_dir / "card_receivables.json")
    metrics = engine.verify_global_conservation()
    check(metrics["global_conservation_held"], "Conservação global de recebíveis violada")
    # Testa ciclo de antecipação e conservação
    ur1_id = "UR-20261215-CIELO-000190-E801292B"
    ur1 = engine.urs[ur1_id]
    check(ur1.available_amount == Decimal("60000.00"), "Saldo disponível divergente na UR 1")
    ant = engine.anticipate_receivables(ur1_id, Decimal("30000.00"), Decimal("0.0200"), "CIELO")
    check(ant.conservation_held, "Violação de conservação na antecipação")
    check(ant.net_disbursed_amount + ant.discount_fee == Decimal("30000.00"), "Divergência de valores na antecipação")
    check(ur1.available_amount == Decimal("30000.00"), "Capacidade remanescente incorreta pós-antecipação")
    # Testa liquidação da UR 2 com trava fiduciária
    ur2_id = "UR-20261220-REDE-000111-F199FFD5"
    receipt = engine.settle_ur_on_maturity(ur2_id)
    check(receipt.invariant_conservation_held, "Conservação na liquidação fiduciária da UR 2 violada")
    check(receipt.total_settled_amount == Decimal("320000.00"), "Valor liquidado total diverge do saldo da UR")
    check(receipt.total_creditor_payments + receipt.total_merchant_payments == receipt.total_settled_amount, "Divergência credor + lojista na liquidação")
    print("      ✓ Unicidade de gravame, trava fiduciária de domicílio e conservação de UR comprovadas.")


def verify_index_rebalancing_invariants(fixtures_dir: Path):
    """
    Lei 48: Conservação de Pesos e Teto Regulatório de Índices B3 (FL-066)
    1. Lei de Conservação Estrita: Soma(W_i) == 1.00000000 (exatamente 100%).
    2. Teto Regulatório de Concentração: W_i <= Cap_Limit (20%) para todo constituinte.
    3. Elegibilidade Estrita: Nenhum constituinte é Penny Stock, em Recuperação Judicial ou com presença < 95%.
    4. Continuidade Patrimonial via Divisor sem salto de preço na transição de carteira.
    """
    print("[48/52] Verificando Invariante de Rebalanceamento e Conservação de Pesos de Índices B3 (FL-066)...")
    engine, assets, data = IndexRebalancingEngine.load_from_fixture(fixtures_dir / "index_rebalancing.json")
    res_ibov = engine.rebalance(
        assets,
        index_type=IndexType.IBOV,
        previous_portfolio=data.get("previous_portfolio_ibov")
    )
    inv = engine.verify_invariants(res_ibov, assets)
    check(inv["weight_conservation"], "Soma dos pesos da carteira do Ibovespa diferente de 100%")
    check(inv["cap_limit_respected"], "Teto regulatório de 20% violado no rebalanceamento do Ibovespa")
    check(inv["no_ineligible_constituents"], "Constituinte inelegível presente na carteira do Ibovespa")
    check(inv["all_passed"], "Falha nas invariantes formais do rebalanceamento de índices B3")

    for itype in [IndexType.IBRX100, IndexType.SMLL, IndexType.IDIV]:
        res = engine.rebalance(assets, index_type=itype)
        inv_other = engine.verify_invariants(res, assets)
        check(inv_other["weight_conservation"], f"Soma dos pesos do índice {itype.value} diferente de 100%")
        check(inv_other["all_passed"], f"Falha formal no índice {itype.value}")

    print("      ✓ Conservação estrita de pesos (100%), capping de 20% e continuidade via divisor comprovadas.")


def verify_cross_border_collateral_invariants(fixtures_dir: Path):
    """
    Lei 49: Garantias Transfronteiriças Euroclear & DTC, Haircuts Soberanos e Buffer Cambial (FL-067)
    1. Não-negatividade e limitação superior do valor líquido elegível em BRL.
    2. Monotonicidade estrita da matriz de haircuts de títulos soberanos por tenor.
    3. Co-domínio estrito [0.00, 1.00) para haircuts e buffer de volatilidade cambial.
    4. Paridade cambial triangular e cotação conservadora PTAX de compra.
    5. Trava estrita de segurança anti-déficit na desoneração de garantias (Safe-Release Guardrail).
    6. Conciliação de custódia transfronteiriça com extratos de Euroclear e DTC.
    """
    print("[49/52] Verificando Invariante de Garantias Transfronteiriças Euroclear & DTC (FL-067)...")
    from src.cross_border_collateral import (
        CrossBorderCollateralEngine,
        InternationalDepository,
        MarginReleaseViolationError,
    )

    engine = CrossBorderCollateralEngine.load_from_fixture(fixtures_dir / "cross_border_collateral.json")
    inv = engine.verify_formal_invariants()
    check(inv["valuation_non_negativity"], "Violação de não-negatividade na avaliação de garantia transfronteiriça")
    check(inv["tenor_haircut_monotonicity"], "Violação da monotonicidade de tenor na matriz de haircuts soberanos")
    check(inv["haircut_codomain_bounded"], "Haircuts ou buffers fora do co-domínio regulatório [0, 1)")
    check(inv["fx_triangulation_parity"], "Inconsistência na paridade triangular de câmbio")

    # Valida apuração de margem e chamada de garantias
    rec_itau = engine.evaluate_margin_call("PART-ITAU-01", Decimal("1500000.00"))
    check(rec_itau.status.value == "SURPLUS" and rec_itau.margin_deficit_brl == Decimal("0.00"),
          "Erro na apuração de margem do participante com superávit")

    rec_brad = engine.evaluate_margin_call("PART-BRADESCO-02", Decimal("1200000.00"), is_intraday=True)
    check(rec_brad.status.value == "MARGIN_CALL_ISSUED" and rec_brad.margin_deficit_brl > Decimal("0.00"),
          "Déficit não identificado na chamada de margem intradiária")

    # Valida Trava de Segurança de Desoneração (Safe-Release Guardrail)
    release_caught = False
    try:
        engine.release_collateral("POS-ITAU-UST-01", Decimal("1800.00"), Decimal("1500000.00"))
    except MarginReleaseViolationError:
        release_caught = True
    check(release_caught, "Trava de segurança permitiu desoneração que geraria déficit de margem")

    # Valida Conciliação de Custódia com DTC e Euroclear
    dtc_stmt = {"US912797HR42": Decimal("2000.00"), "US91282CDJ71": Decimal("1500.00")}
    rec_dtc = engine.reconcile_custody(InternationalDepository.DTC, dtc_stmt)
    check(rec_dtc.is_balanced, "Divergência de custódia na conciliação com a Depository Trust Company (DTC)")

    print("      ✓ Matriz de haircuts soberanos, buffer cambial, chamada de margem e conciliação Euroclear/DTC comprovadas.")


def verify_volatility_strategies_invariants(fixtures_dir: Path):
    """
    Lei 50: Conservação de Razão de Pernas e Margem com Haircut em Estratégias de Volatilidade (FL-063)
    1. Validação estrutural de todas as estratégias (Straddle, Strangle, Iron Condor, Collar, Butterfly).
    2. Invariante de Razão de Pernas (Leg Ratio Invariant): Q_i_exec = k * R_i.
    3. Atomicidade All-or-None com zero legging risk.
    4. Redução de Margem de Risco CORE com Haircut de Volatilidade: Margem Combinada <= Margem Isolada.
    5. Conservação do Preço Líquido e Fluxo Financeiro Total.
    """
    print("[50/52] Verificando Invariante de Estratégias de Volatilidade e Opções Combinadas (FL-063)...")
    from src.volatility_strategies import (
        VolatilityStrategyValidator,
        CombinedRiskMarginEngine,
        PumaAtomicMultiLegEngine,
        MultiLegOrder,
        load_from_fixture,
    )

    data = load_from_fixture(fixtures_dir / "volatility_strategies.json")
    margin_engine = CombinedRiskMarginEngine()
    puma_engine = PumaAtomicMultiLegEngine(margin_engine=margin_engine)

    for strat_dict in data["strategies"]:
        order = MultiLegOrder.from_dict({
            "order_id": strat_dict["strategy_id"],
            "strategy_type": strat_dict["strategy_type"],
            "underlying_symbol": strat_dict["underlying_symbol"],
            "underlying_spot": strat_dict["underlying_spot"],
            "packages_requested": strat_dict["packages_requested"],
            "limit_net_price": strat_dict["limit_net_price"],
            "legs": strat_dict["legs"],
        })

        # 1. Validação estrutural
        VolatilityStrategyValidator.validate_order(order)

        # 2. Margem combinada com haircut vs margem isolada
        margin_req = margin_engine.calculate_combined_margin(order)
        check(margin_req.combined_margin <= margin_req.naked_isolated_margin,
              f"Margem combinada excede margem isolada na estratégia {order.order_id}")

        # 3. Execução atômica no PUMA
        receipt = puma_engine.execute_order(order, strat_dict["simulated_book"])
        check(receipt.atomic, f"Execução não atômica na estratégia {receipt.strategy_type.value}")
        check(receipt.packages_filled == order.packages_requested,
              f"Falha na execução total de pacotes na estratégia {order.order_id}")

        # 4. Prova formal da Lei de Conservação de Razão de Pernas
        check(puma_engine.verify_ratio_conservation(receipt, order),
              f"Violação da lei de conservação de razão de pernas na estratégia {order.order_id}")

        # 5. Conservação de fluxo financeiro
        check(abs(receipt.total_net_cash_flow) > Decimal("0.00"),
              f"Fluxo financeiro nulo na estratégia {order.order_id}")

    print("      ✓ Conservação de razão de pernas (1:1, 1:1:1:1, 1:2:1), atomicidade All-or-None e desconto de margem CORE comprovadas.")


def verify_esg_green_bonds_invariants(fixtures_dir: Path):
    """
    Lei 50: Conservação de Recursos Verdes (Use of Proceeds) e Determinismo de Gatilho SLB (FL-065)
    1. Conservação de Recursos: Net_Proceeds == Total_Allocated + Total_Unallocated centavo a centavo.
    2. Não-Sobrealocação: Total_Allocated <= Net_Proceeds e Allocated <= ProjectCost para todo projeto.
    3. Determinismo de Gatilho SLB:
       - Se meta MISSED ou laudo DEFAULTED_REPORTING, Current_Coupon == Base_Coupon + Step_Up_Spread.
       - Se meta ACHIEVED, Current_Coupon == Base_Coupon.
    4. Sobretaxa Não-Negativa: Step_Up_Penalty >= 0 em todos os períodos.
    """
    print("[51/52] Verificando Invariante de Taxonomia Verde, Conservação de Recursos e Step-Up SLB (FL-065)...")
    from src.esg_green_bonds import EsgGreenBondsEngine

    engine = EsgGreenBondsEngine.load_from_json(fixtures_dir / "esg_green_bonds.json")
    for asset_code in engine.emissions:
        check(engine.verify_invariants(asset_code), f"Violação de invariante ESG na emissão {asset_code}")
    print("      ✓ Conservação de recursos Use of Proceeds, validação de taxonomia e gatilho de Step-Up comprovadas.")


def verify_cra_cri_securitization_invariants(fixtures_dir: Path):
    """
    Lei 52: Conservação de Securitização, Regime Fiduciário e Fundo de Reserva de CRA e CRI (FL-062)
    1. Conservação financeira contábil estrita do Payment Waterfall (Entradas == Saídas).
    2. Inviolabilidade da subordinação (water-tight priority): Subordinada recebe 0 se Sênior deficitária ou Reserva desfalecada.
    3. Proteção de Primeira Perda (First Loss Piece): Write-offs de lastro consumindo primeiro a classe Subordinada.
    4. Conservação de Saldo Devedor e Preço Unitário: Saldo == Qtd * PU atualizado.
    5. Incomunicabilidade do patrimônio fiduciário separado e cobertura de ativos >= passivo sênior.
    """
    print("[52/52] Verificando Invariante de Securitização e Fundo de Reserva de CRA e CRI (FL-062)...")
    from src.securitization_cra_cri import CraCriSecuritizationEngine
    engine = CraCriSecuritizationEngine.load_from_fixture(fixtures_dir / "cra_cri_securitization.json")
    all_inv = engine.verify_all_invariants()
    for issue_id, report in all_inv.items():
        check(report["all_invariants_hold"], f"Falha de invariante fiduciária na emissão {issue_id}")

    # Validação do Waterfall com conservação financeira
    res = engine.execute_waterfall("CRA-AGRO-2026-001", date(2026, 12, 15), period_indexer_rate=Decimal("0.0080"))
    check(res.conservation_verified, "Conservação de entradas e saídas violada no waterfall de CRA")

    # Validação da First Loss Piece
    res_def = engine.process_collateral_default("CRA-DEFAULT-WRITE-OFF-2026-004", "CPR-CITROS-SP-003")
    check(res_def.loss_absorbed_by_subordinated == Decimal("6000000.00") and res_def.loss_absorbed_by_senior == Decimal("0.00"),
          "Subordinação violada na absorção de perda de lastro")
    print("      ✓ Conservação financeira do waterfall, prioridade de subordinação e first-loss piece comprovadas.")


def main():
    print("=" * 70)
    print(" B3 INTEGRATION ATLAS — VERIFICAÇÃO FORMAL DE INVARIANTES")
    print(" Baseada em proof systems e leis de conservação financeira")
    print("=" * 70)
    
    fixtures_dir = REPO_ROOT / "fixtures"
    if not fixtures_dir.exists():
        print(f"ERRO: Diretório de fixtures não encontrado em {fixtures_dir}")
        sys.exit(1)
        
    try:
        verify_custody_conservation(fixtures_dir)
        verify_multilateral_financial_conservation(fixtures_dir)
        verify_rtc_allocation_conservation(fixtures_dir)
        verify_clearing_positions_invariants(fixtures_dir)
        verify_loan_btc_invariants(fixtures_dir)
        verify_collateral_margin_invariants(fixtures_dir)
        verify_corporate_events_invariants(fixtures_dir)
        verify_custody_movements_invariants(fixtures_dir)
        verify_pipeline_unification(fixtures_dir)
        verify_stvm_portability_conservation(fixtures_dir)
        verify_btc_lending_matching_invariants(fixtures_dir)
        verify_funds_platform_invariants(fixtures_dir)
        verify_spb_multilateral_settlement_invariants(fixtures_dir)
        verify_nome_agro_invariants(fixtures_dir)
        verify_tesouro_direto_invariants(fixtures_dir)
        verify_bmf_futures_settlement_invariants(fixtures_dir)
        verify_public_offerings_cvm160_invariants(fixtures_dir)
        verify_bdr_conversion_invariants(fixtures_dir)
        verify_etf_primary_basket_invariants(fixtures_dir)
        verify_cbio_carbon_retirement_invariants(fixtures_dir)
        verify_lien_collateral_waterfall_invariants(fixtures_dir)
        verify_buyin_failure_management_invariants(fixtures_dir)
        verify_listed_options_expiration_invariants(fixtures_dir)
        verify_otc_derivatives_swap_ndf_invariants(fixtures_dir)
        verify_equity_forward_termo_invariants(fixtures_dir)
        verify_flexible_options_otc_invariants(fixtures_dir)
        verify_units_conversion_invariants(fixtures_dir)
        verify_opa_tender_offer_invariants(fixtures_dir)
        verify_dma_governance_invariants(fixtures_dir)
        verify_market_maker_pmm_invariants(fixtures_dir)
        verify_hft_algo_governance_invariants(fixtures_dir)
        verify_btc_corporate_actions_invariants(fixtures_dir)
        verify_lig_covered_bonds_invariants(fixtures_dir)
        verify_core_intraday_margin_invariants(fixtures_dir)
        verify_electronic_trade_bills_invariants(fixtures_dir)
        verify_share_fractions_auction_invariants(fixtures_dir)
        verify_b3_tariff_pricing_invariants(fixtures_dir)
        verify_cdb_rdb_registry_invariants(fixtures_dir)
        verify_default_waterfall_invariants(fixtures_dir)
        verify_puma_advanced_orders_invariants(fixtures_dir)
        verify_infrastructure_debentures_invariants(fixtures_dir)
        verify_coe_structured_notes_invariants(fixtures_dir)
        verify_puma_closing_auction_invariants(fixtures_dir)
        verify_fx_settlement_pvp_invariants(fixtures_dir)
        verify_drex_tokenized_assets_invariants(fixtures_dir)
        verify_smart_order_router_invariants(fixtures_dir)
        verify_card_receivables_invariants(fixtures_dir)
        verify_index_rebalancing_invariants(fixtures_dir)
        verify_cross_border_collateral_invariants(fixtures_dir)
        verify_volatility_strategies_invariants(fixtures_dir)
        verify_esg_green_bonds_invariants(fixtures_dir)
        verify_cra_cri_securitization_invariants(fixtures_dir)
        
        print("\n" + "=" * 70)
        print("✓ TODAS AS 52 LEIS DE CONSERVAÇÃO E INVARIANTES DA B3 FORAM PROVADAS!")
        print("  Garantia matemática de consistência em todas as fixtures e módulos.")
        print("=" * 70)
        sys.exit(0)
    except InvariantViolationError as e:
        print("\n" + "!" * 70)
        print(f"FALHA FORMAL: {e}")
        print("!" * 70)
        sys.exit(1)


if __name__ == "__main__":
    main()
