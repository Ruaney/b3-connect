"""Bateria de Testes Unitários e Provas de Invariantes Formais para Créditos de Carbono Voluntário B3 (FL-088).

Valida conformidade estrita com:
- Padrões Internacionais: VCS (Verra), Gold Standard (GS), ACR, CAR, Cercarbono e Padrão B3;
- Tipologias de Projeto: REDD+, ARR, Bioenergia/Biomassa, Energia Renovável e DAC;
- Lei nº 12.187/2009 (PNMC) e Lei nº 14.119/2021 (PSA);
- Compensação Corporativa GHG Protocol (Escopos 1, 2, 3 e Net Zero);
- Invariantes de Unicidade, Anti-Double Counting, Não-Negatividade de Saldos, Liquidação Financeira DvP e Aposentadoria Irrevogável.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import unittest

from src.voluntary_carbon_credits import (
    CarbonCreditBatch,
    CarbonCreditStatus,
    CarbonInvariantViolationError,
    CarbonOffsetScope,
    CarbonProject,
    CarbonProjectType,
    CarbonRetirementCertificate,
    CarbonSerialRange,
    CarbonValidationError,
    CertificationStandard,
    CustodyAccount,
    CustodyAccountType,
    DoubleCountingError,
    DvPSettlementError,
    DvPTrade,
    DvPTradeStatus,
    InsufficientCarbonBalanceError,
    InvalidRetirementError,
    VoluntaryCarbonCreditError,
    VoluntaryCarbonCreditsEngine,
    round_cents,
    round_tco2e,
    sanitize_document,
    to_decimal,
)


class TestVoluntaryCarbonCredits(unittest.TestCase):
    """Suíte completa de validação funcional e integridade matemática de créditos de carbono voluntário B3."""

    def setUp(self) -> None:
        self.fixture_path = (
            Path(__file__).resolve().parent.parent / "fixtures" / "voluntary_carbon_credits.json"
        )
        self.engine = VoluntaryCarbonCreditsEngine(default_b3_fee_rate="0.0010")

    # -----------------------------------------------------------------
    # 1. CARREGAMENTO DE FIXTURE E INTEGRIDADE GLOBAL
    # -----------------------------------------------------------------

    def test_fixture_loading_and_invariants(self) -> None:
        """Verifica se a fixture oficial é carregada e atende a 100% das invariantes formais."""
        self.assertTrue(self.fixture_path.exists(), f"Fixture não encontrada em {self.fixture_path}")
        with open(self.fixture_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertEqual(data["flow_id"], "FL-088")
        self.assertEqual(data["schema_version"], "1.0.0")

        engine = VoluntaryCarbonCreditsEngine()
        engine.load_from_dict(data["engine_state"])

        # Auditoria formal
        audit = engine.verify_invariants()
        self.assertTrue(audit["INV-VCM-001_ANTI_DOUBLE_COUNTING"])
        self.assertTrue(audit["INV-VCM-002_NON_NEGATIVE_BALANCES"])
        self.assertTrue(audit["INV-VCM-003_DVP_FINANCIAL_SETTLEMENT"])
        self.assertTrue(audit["INV-VCM-004_IRREVOCABLE_RETIREMENT"])
        self.assertTrue(audit["INV-VCM-005_METRIC_PARITY"])
        engine.assert_invariants()

        # Verifica quantidade de entidades carregadas
        self.assertEqual(len(engine.projects), 5)
        self.assertEqual(len(engine.batches), 5)
        self.assertEqual(len(engine.custody_accounts), 4)
        self.assertEqual(len(engine.trades), 3)
        self.assertEqual(len(engine.retirement_certificates), 2)

    # -----------------------------------------------------------------
    # 2. CADASTRO DE PROJETOS E VALIDAÇÃO DE PADRÕES E TIPOLOGIAS
    # -----------------------------------------------------------------

    def test_project_registration_and_types(self) -> None:
        """Valida cadastro de projetos nos múltiplos padrões e tipologias exigidas."""
        # VCS REDD+
        p_vcs = self.engine.register_project(
            project_id="PRJ-VCS-01",
            name="Projeto Conservação Floresta Amazônica",
            standard=CertificationStandard.VCS,
            project_type=CarbonProjectType.REDD_PLUS,
            country="BR",
            state_or_province="AM",
            developer_name="Amazônia Sustentável Ltda.",
            registry_url_or_id="https://verra.org/prj/01",
            methodology="VM0007",
            validator_body="SCS Global Services",
        )
        self.assertEqual(p_vcs.standard, CertificationStandard.VCS)
        self.assertEqual(p_vcs.project_type, CarbonProjectType.REDD_PLUS)

        # Gold Standard Bioenergia
        p_gs = self.engine.register_project(
            project_id="PRJ-GS-01",
            name="Projeto Bioeletricidade de Bagaço",
            standard=CertificationStandard.GOLD_STANDARD,
            project_type=CarbonProjectType.BIOENERGY_BIOMASS,
            country="BR",
            state_or_province="SP",
            developer_name="Bioenergia Paulista S.A.",
            registry_url_or_id="https://goldstandard.org/prj/01",
            methodology="ACM0006",
            validator_body="TÜV NORD",
        )
        self.assertEqual(p_gs.standard, CertificationStandard.GOLD_STANDARD)
        self.assertEqual(p_gs.project_type, CarbonProjectType.BIOENERGY_BIOMASS)

        # Cercarbono Reflorestamento (ARR)
        p_cer = self.engine.register_project(
            project_id="PRJ-CER-01",
            name="Projeto Reflorestamento Cerrado",
            standard=CertificationStandard.CERCARBONO,
            project_type=CarbonProjectType.ARR,
            country="BR",
            state_or_province="GO",
            developer_name="Cerrado Verde Agroflorestal",
            registry_url_or_id="https://cercarbono.com/prj/01",
            methodology="BCR-0001",
            validator_body="AENOR",
        )
        self.assertEqual(p_cer.standard, CertificationStandard.CERCARBONO)
        self.assertEqual(p_cer.project_type, CarbonProjectType.ARR)

        # ACR Captura Direta de Ar (DAC)
        p_acr = self.engine.register_project(
            project_id="PRJ-ACR-01",
            name="Projeto DAC Mineralização Basáltica",
            standard=CertificationStandard.ACR,
            project_type=CarbonProjectType.DAC,
            country="BR",
            state_or_province="PR",
            developer_name="Carbon Capture Tech",
            registry_url_or_id="https://acr.winrock.org/01",
            methodology="ACR-CCS",
            validator_body="Bureau Veritas",
        )
        self.assertEqual(p_acr.standard, CertificationStandard.ACR)
        self.assertEqual(p_acr.project_type, CarbonProjectType.DAC)

        # CAR e Padrão B3 Energia Renovável
        p_b3 = self.engine.register_project(
            project_id="PRJ-B3-01",
            name="Complexo Solar Sertão Limpo",
            standard=CertificationStandard.PADRAO_B3,
            project_type=CarbonProjectType.RENEWABLE_ENERGY,
            country="BR",
            state_or_province="PI",
            developer_name="Sertão Solar B3 S.A.",
            registry_url_or_id="https://carbono.b3.com.br/01",
            methodology="B3-SOLAR-01",
            validator_body="DNV GL",
        )
        self.assertEqual(p_b3.standard, CertificationStandard.PADRAO_B3)
        self.assertEqual(p_b3.project_type, CarbonProjectType.RENEWABLE_ENERGY)

        # Tentativa de duplicar project_id deve falhar
        with self.assertRaises(CarbonValidationError):
            self.engine.register_project(
                project_id="PRJ-VCS-01",
                name="Duplicata",
                standard=CertificationStandard.VCS,
                project_type=CarbonProjectType.REDD_PLUS,
                country="BR",
                state_or_province="AM",
                developer_name="X",
                registry_url_or_id="Y",
                methodology="Z",
                validator_body="W",
            )

    # -----------------------------------------------------------------
    # 3. ESCRITURAÇÃO DE LOTES E PARIDADE METROLÓGICA (1 CRÉDITO = 1 tCO2e)
    # -----------------------------------------------------------------

    def test_batch_issuance_and_paridade(self) -> None:
        """Verifica a escrituração primária, paridade serial e validação de parâmetros."""
        self.engine.register_project(
            project_id="PRJ-ARR-01",
            name="Reflorestamento Vale Verde",
            standard=CertificationStandard.VCS,
            project_type=CarbonProjectType.ARR,
            country="BR",
            state_or_province="MG",
            developer_name="Vale Verde S.A.",
            registry_url_or_id="https://verra.org/arr01",
            methodology="VM0047",
            validator_body="SCS Global",
        )

        # Lote válido com 10.000 créditos (10.000 tCO2e)
        batch = self.engine.register_batch(
            batch_id="BATCH-ARR-2023-01",
            project_id="PRJ-ARR-01",
            vintage=2023,
            serial_prefix="VCS-ARR-2023-BR",
            serial_start=1,
            serial_end=10000,
            total_issued_tco2e=Decimal("10000.00"),
            verifier_report_hash="a" * 64,
        )
        self.assertEqual(batch.total_issued_tco2e, Decimal("10000.00"))
        self.assertEqual(batch.available_tco2e, Decimal("10000.00"))
        self.assertEqual(batch.locked_tco2e, Decimal("0.00"))
        self.assertEqual(batch.retired_tco2e, Decimal("0.00"))

        # Falha se total_units serial difere do volume emitido
        with self.assertRaises(CarbonValidationError):
            self.engine.register_batch(
                batch_id="BATCH-ARR-2023-02",
                project_id="PRJ-ARR-01",
                vintage=2023,
                serial_prefix="VCS-ARR-2023-BR-DIV",
                serial_start=1,
                serial_end=5000,
                total_issued_tco2e=Decimal("10000.00"),  # 5000 != 10000
            )

        # Falha com vintage inválida
        with self.assertRaises(CarbonValidationError):
            self.engine.register_batch(
                batch_id="BATCH-ARR-INV-VINTAGE",
                project_id="PRJ-ARR-01",
                vintage=1980,  # Vintage anterior ao ano 2000
                serial_prefix="VCS-ARR-INV",
                serial_start=1,
                serial_end=1000,
                total_issued_tco2e=Decimal("1000.00"),
            )

        # Falha com hash inválido (não SHA-256)
        with self.assertRaises(CarbonValidationError):
            self.engine.register_batch(
                batch_id="BATCH-ARR-INV-HASH",
                project_id="PRJ-ARR-01",
                vintage=2024,
                serial_prefix="VCS-ARR-INVH",
                serial_start=1,
                serial_end=1000,
                total_issued_tco2e=Decimal("1000.00"),
                verifier_report_hash="invalid_hash_short",
            )

    # -----------------------------------------------------------------
    # 4. INVARIANTE ANTI-DOUBLE COUNTING (NÃO-DUPLA CONTAGEM)
    # -----------------------------------------------------------------

    def test_anti_double_counting_serial_overlap(self) -> None:
        """Garante que intervalos seriais de mesmo standard e projeto não podem colidir ou sobrepor."""
        self.engine.register_project(
            project_id="PRJ-DAC-01",
            name="Captura Direta de Carbono Basalto",
            standard=CertificationStandard.CAR,
            project_type=CarbonProjectType.DAC,
            country="BR",
            state_or_province="SC",
            developer_name="Basalto Carbon",
            registry_url_or_id="https://climateactionreserve.org/dac",
            methodology="CAR-DAC",
            validator_body="AENOR",
        )

        # Primeiro lote: seriais 1 a 10.000
        self.engine.register_batch(
            batch_id="BATCH-CAR-01",
            project_id="PRJ-DAC-01",
            vintage=2024,
            serial_prefix="CAR-DAC-2024",
            serial_start=1,
            serial_end=10000,
            total_issued_tco2e="10000.00",
        )

        # Tentativa de emitir lote com sobreposição serial parcial (8.000 a 15.000)
        with self.assertRaises(DoubleCountingError) as ctx:
            self.engine.register_batch(
                batch_id="BATCH-CAR-02",
                project_id="PRJ-DAC-01",
                vintage=2024,
                serial_prefix="CAR-DAC-2024",
                serial_start=8000,
                serial_end=15000,
                total_issued_tco2e="7001.00",
            )
        self.assertIn("Colisão de intervalo serial", str(ctx.exception))

        # Lote subsequente sem sobreposição (10.001 a 20.000) deve passar com sucesso
        b2 = self.engine.register_batch(
            batch_id="BATCH-CAR-02-VALID",
            project_id="PRJ-DAC-01",
            vintage=2024,
            serial_prefix="CAR-DAC-2024",
            serial_start=10001,
            serial_end=20000,
            total_issued_tco2e="10000.00",
        )
        self.assertEqual(b2.serial_range.start_index, 10001)

    # -----------------------------------------------------------------
    # 5. CONTAS DE CUSTÓDIA SEGREGADA E NÃO-NEGATIVIDADE DE SALDOS
    # -----------------------------------------------------------------

    def test_custody_account_and_non_negativity(self) -> None:
        """Verifica a abertura de contas com CPF/CNPJ e invariante estrita de não-negatividade."""
        # Abertura com CNPJ formatado
        acc1 = self.engine.open_custody_account(
            account_id="ACC-01",
            participant_code="0088",
            holder_name="Empresa A",
            holder_document="12.345.678/0001-95",
            account_type=CustodyAccountType.PROPRIETARY,
        )
        self.assertEqual(acc1.holder_document, "12345678000195")

        # Abertura com CPF
        acc2 = self.engine.open_custody_account(
            account_id="ACC-02",
            participant_code="0072",
            holder_name="Pessoa B",
            holder_document="123.456.789-00",
            account_type=CustodyAccountType.CLIENT,
        )
        self.assertEqual(acc2.holder_document, "12345678900")

        # Documento inválido deve falhar
        with self.assertRaises(CarbonValidationError):
            self.engine.open_custody_account(
                account_id="ACC-INV",
                participant_code="0088",
                holder_name="Invalido",
                holder_document="12345",
            )

        # Depósito e movimentação
        self.engine.register_project(
            project_id="PRJ-BIO-01",
            name="Biomassa Florestal",
            standard=CertificationStandard.PADRAO_B3,
            project_type=CarbonProjectType.BIOENERGY_BIOMASS,
            country="BR",
            state_or_province="MS",
            developer_name="Bio MS S.A.",
            registry_url_or_id="https://carbono.b3.com.br/bio",
            methodology="B3-BIO-01",
            validator_body="DNV GL",
        )
        batch = self.engine.register_batch(
            batch_id="BATCH-BIO-01",
            project_id="PRJ-BIO-01",
            vintage=2023,
            serial_prefix="B3-BIO-2023",
            serial_start=1,
            serial_end=5000,
            total_issued_tco2e="5000.00",
        )

        self.engine.deposit_initial_batch("ACC-01", "BATCH-BIO-01", "5000.00")
        pos = acc1.get_position("BATCH-BIO-01")
        self.assertEqual(pos.available_quantity, Decimal("5000.00"))

        # Tentativa de débito além do saldo disponível levanta InsufficientCarbonBalanceError
        with self.assertRaises(InsufficientCarbonBalanceError):
            acc1.debit("BATCH-BIO-01", "5001.00")

        # Bloqueio de créditos
        self.engine.lock_credits("ACC-01", "BATCH-BIO-01", "1500.00")
        self.assertEqual(pos.available_quantity, Decimal("3500.00"))
        self.assertEqual(pos.locked_quantity, Decimal("1500.00"))
        self.assertEqual(batch.available_tco2e, Decimal("3500.00"))
        self.assertEqual(batch.locked_tco2e, Decimal("1500.00"))

        # Tentativa de desbloqueio além do saldo bloqueado deve falhar
        with self.assertRaises(InsufficientCarbonBalanceError):
            self.engine.unlock_credits("ACC-01", "BATCH-BIO-01", "1500.01")

        # Desbloqueio correto
        self.engine.unlock_credits("ACC-01", "BATCH-BIO-01", "500.00")
        self.assertEqual(pos.locked_quantity, Decimal("1000.00"))
        self.assertEqual(pos.available_quantity, Decimal("4000.00"))

        # Transferência bilateral simples
        self.engine.transfer_custody("ACC-01", "ACC-02", "BATCH-BIO-01", "1000.00")
        self.assertEqual(pos.available_quantity, Decimal("3000.00"))
        pos2 = acc2.get_position("BATCH-BIO-01")
        self.assertEqual(pos2.available_quantity, Decimal("1000.00"))

    # -----------------------------------------------------------------
    # 6. NEGOCIAÇÃO BILATERAL EM BALCÃO E LIQUIDAÇÃO FINANCEIRA DvP
    # -----------------------------------------------------------------

    def test_dvp_bilateral_trade_settlement(self) -> None:
        """Valida execução atômica da liquidação Delivery versus Payment com conservação financeira."""
        self.engine.register_project(
            project_id="PRJ-RE-01",
            name="Parque Eólico Litoral",
            standard=CertificationStandard.VCS,
            project_type=CarbonProjectType.RENEWABLE_ENERGY,
            country="BR",
            state_or_province="RN",
            developer_name="Ventos do Norte S.A.",
            registry_url_or_id="https://verra.org/re01",
            methodology="ACM0002",
            validator_body="SCS Global",
        )
        self.engine.register_batch(
            batch_id="BATCH-WIND-01",
            project_id="PRJ-RE-01",
            vintage=2024,
            serial_prefix="VCS-WIND-2024",
            serial_start=1,
            serial_end=20000,
            total_issued_tco2e="20000.00",
        )

        acc_seller = self.engine.open_custody_account(
            account_id="ACC-SELLER",
            participant_code="0088",
            holder_name="Geradora Eólica S.A.",
            holder_document="11222333000144",
        )
        acc_buyer = self.engine.open_custody_account(
            account_id="ACC-BUYER",
            participant_code="0072",
            holder_name="Comprador Institucional S.A.",
            holder_document="44555666000177",
        )

        self.engine.deposit_initial_batch("ACC-SELLER", "BATCH-WIND-01", "20000.00")

        # Executa operação DvP de 6.000 créditos a R$ 75,50 com taxa B3 de 0,10%
        trade = self.engine.execute_dvp_trade(
            trade_id="TRD-DVP-001",
            batch_id="BATCH-WIND-01",
            seller_account_id="ACC-SELLER",
            buyer_account_id="ACC-BUYER",
            quantity_tco2e=Decimal("6000.00"),
            unit_price_brl=Decimal("75.50"),
            b3_fee_rate=Decimal("0.001000"),
        )

        # Checagem financeira
        expected_gross = Decimal("453000.00")  # 6000 * 75.50
        expected_fee = Decimal("453.00")       # 453000 * 0.0010
        expected_buyer_disbursement = Decimal("453453.00")

        self.assertEqual(trade.gross_amount_brl, expected_gross)
        self.assertEqual(trade.b3_fee_brl, expected_fee)
        self.assertEqual(trade.net_seller_amount_brl, expected_gross)
        self.assertEqual(trade.net_buyer_disbursement_brl, expected_buyer_disbursement)
        self.assertEqual(trade.status, DvPTradeStatus.SETTLED)

        # Checagem da conservação de saldos pós-DvP
        pos_seller = acc_seller.get_position("BATCH-WIND-01")
        pos_buyer = acc_buyer.get_position("BATCH-WIND-01")
        self.assertEqual(pos_seller.available_quantity, Decimal("14000.00"))
        self.assertEqual(pos_buyer.available_quantity, Decimal("6000.00"))

        # Tentativa de DvP sem saldo suficiente pelo vendedor deve falhar
        with self.assertRaises(InsufficientCarbonBalanceError):
            self.engine.execute_dvp_trade(
                trade_id="TRD-DVP-002-FAIL",
                batch_id="BATCH-WIND-01",
                seller_account_id="ACC-SELLER",
                buyer_account_id="ACC-BUYER",
                quantity_tco2e=Decimal("15000.00"),  # Seller só tem 14000
                unit_price_brl=Decimal("75.50"),
            )

        # Tentativa de DvP com vendedor igual a comprador
        with self.assertRaises(CarbonValidationError):
            self.engine.execute_dvp_trade(
                trade_id="TRD-DVP-SAME",
                batch_id="BATCH-WIND-01",
                seller_account_id="ACC-SELLER",
                buyer_account_id="ACC-SELLER",
                quantity_tco2e=Decimal("100.00"),
                unit_price_brl=Decimal("75.50"),
            )

    # -----------------------------------------------------------------
    # 7. APOSENTADORIA DEFINITIVA IRREVOGÁVEL E CERTIDÃO DE COMPENSAÇÃO
    # -----------------------------------------------------------------

    def test_irrevocable_retirement_and_certificate(self) -> None:
        """Valida aposentadoria irrevogável para Scope 1, 2 e 3 e emissão de certidão SHA-256."""
        self.engine.register_project(
            project_id="PRJ-REDD-01",
            name="Jari Amapá REDD+ Project",
            standard=CertificationStandard.VCS,
            project_type=CarbonProjectType.REDD_PLUS,
            country="BR",
            state_or_province="AP",
            developer_name="Jari Florestal S.A.",
            registry_url_or_id="https://verra.org/jari",
            methodology="VM0007",
            validator_body="SCS Global",
        )
        batch = self.engine.register_batch(
            batch_id="BATCH-JARI-01",
            project_id="PRJ-REDD-01",
            vintage=2023,
            serial_prefix="VCS-JARI-2023",
            serial_start=1,
            serial_end=10000,
            total_issued_tco2e="10000.00",
        )

        acc = self.engine.open_custody_account(
            account_id="ACC-IND-01",
            participant_code="0088",
            holder_name="Indústria Química Nacional S.A.",
            holder_document="33444555000166",
            account_type=CustodyAccountType.OFFSETTING_CORPORATE,
        )
        self.engine.deposit_initial_batch("ACC-IND-01", "BATCH-JARI-01", "10000.00")

        # 1. Aposentadoria de 2.500 tCO2e para Escopo 1 (Caldeiras a gás)
        cert1 = self.engine.retire_credits(
            certificate_id="CERT-APOS-001",
            account_id="ACC-IND-01",
            batch_id="BATCH-JARI-01",
            quantity_tco2e=Decimal("2500.00"),
            beneficiary_name="Indústria Química Nacional S.A.",
            beneficiary_document="33444555000166",
            scope=CarbonOffsetScope.SCOPE_1,
            retirement_reason="Compensação de emissões fugitivas e caldeiras industriais de Escopo 1",
            retirement_timestamp="2026-03-20T10:00:00+00:00",
        )

        self.assertTrue(cert1.is_irrevocable)
        self.assertEqual(cert1.quantity_tco2e, Decimal("2500.00"))
        self.assertEqual(cert1.scope, CarbonOffsetScope.SCOPE_1)
        self.assertEqual(cert1.serial_subrange, "VCS-JARI-2023-00000001-00002500")
        self.assertEqual(len(cert1.certificate_hash), 64)

        # 2. Aposentadoria de 1.500 tCO2e para Escopo 2 (Eletricidade do SIN)
        cert2 = self.engine.retire_credits(
            certificate_id="CERT-APOS-002",
            account_id="ACC-IND-01",
            batch_id="BATCH-JARI-01",
            quantity_tco2e=Decimal("1500.00"),
            beneficiary_name="Indústria Química Nacional S.A.",
            beneficiary_document="33444555000166",
            scope=CarbonOffsetScope.SCOPE_2,
            retirement_reason="Compensação de emissões de Escopo 2 pelo fator médio do SIN",
            retirement_timestamp="2026-03-21T11:00:00+00:00",
        )
        self.assertEqual(cert2.serial_subrange, "VCS-JARI-2023-00002501-00004000")

        # 3. Aposentadoria de 6.000 tCO2e para Escopo 3 (Logística de frete terceirizado)
        cert3 = self.engine.retire_credits(
            certificate_id="CERT-APOS-003",
            account_id="ACC-IND-01",
            batch_id="BATCH-JARI-01",
            quantity_tco2e=Decimal("6000.00"),
            beneficiary_name="Indústria Química Nacional S.A.",
            beneficiary_document="33444555000166",
            scope=CarbonOffsetScope.SCOPE_3,
            retirement_reason="Compensação da frota de transporte terceirizada de insumos químicos",
            retirement_timestamp="2026-03-22T14:00:00+00:00",
        )
        self.assertEqual(cert3.serial_subrange, "VCS-JARI-2023-00004001-00010000")

        # Checagem de exaustão do lote e status final
        self.assertEqual(batch.available_tco2e, Decimal("0.00"))
        self.assertEqual(batch.locked_tco2e, Decimal("0.00"))
        self.assertEqual(batch.retired_tco2e, Decimal("10000.00"))
        self.assertEqual(batch.status, CarbonCreditStatus.RETIRED)

        # Tentativa de aposentar mais créditos de um lote exaurido deve falhar
        with self.assertRaises(InsufficientCarbonBalanceError):
            self.engine.retire_credits(
                certificate_id="CERT-APOS-004-EXHAUSTED",
                account_id="ACC-IND-01",
                batch_id="BATCH-JARI-01",
                quantity_tco2e=Decimal("1.00"),
                beneficiary_name="Indústria Química Nacional S.A.",
                beneficiary_document="33444555000166",
                scope=CarbonOffsetScope.NET_ZERO_CORPORATE,
                retirement_reason="Tentativa ilegal",
            )

    # -----------------------------------------------------------------
    # 8. FRACIONAMENTO DECIMAL DE CRÉDITOS DE CARBONO (0,01 tCO2e)
    # -----------------------------------------------------------------

    def test_fractional_tco2e_custody_and_retirement(self) -> None:
        """Verifica a capacidade do motor em suportar frações de tCO2e sem erros de ponto flutuante."""
        self.engine.register_project(
            project_id="PRJ-SOLAR-01",
            name="Usina Fotovoltaica Sertão Solar",
            standard=CertificationStandard.CERCARBONO,
            project_type=CarbonProjectType.RENEWABLE_ENERGY,
            country="BR",
            state_or_province="CE",
            developer_name="Sertão Solar Ltda.",
            registry_url_or_id="https://cercarbono.com/solar",
            methodology="BCR-0002",
            validator_body="AENOR",
        )
        self.engine.register_batch(
            batch_id="BATCH-SOLAR-01",
            project_id="PRJ-SOLAR-01",
            vintage=2024,
            serial_prefix="CER-SOLAR-2024",
            serial_start=1,
            serial_end=1000,
            total_issued_tco2e="1000.00",
        )

        acc_seller = self.engine.open_custody_account(
            account_id="ACC-FRAC-SELLER",
            participant_code="0088",
            holder_name="Originador Solar",
            holder_document="77888999000122",
        )
        acc_buyer = self.engine.open_custody_account(
            account_id="ACC-FRAC-BUYER",
            participant_code="0072",
            holder_name="Microempresa EcoTech",
            holder_document="11222333000199",
        )
        self.engine.deposit_initial_batch("ACC-FRAC-SELLER", "BATCH-SOLAR-01", "1000.00")

        # Negociação DvP de fração 12,75 tCO2e a R$ 84,20
        trade = self.engine.execute_dvp_trade(
            trade_id="TRD-FRAC-01",
            batch_id="BATCH-SOLAR-01",
            seller_account_id="ACC-FRAC-SELLER",
            buyer_account_id="ACC-FRAC-BUYER",
            quantity_tco2e=Decimal("12.75"),
            unit_price_brl=Decimal("84.20"),
            b3_fee_rate=Decimal("0.001000"),
        )

        expected_gross = Decimal("1073.55")  # 12.75 * 84.20 = 1073.55
        expected_fee = Decimal("1.07")       # 1073.55 * 0.001 = 1.07355 -> 1.07
        self.assertEqual(trade.gross_amount_brl, expected_gross)
        self.assertEqual(trade.b3_fee_brl, expected_fee)
        self.assertEqual(trade.net_buyer_disbursement_brl, Decimal("1074.62"))

        # Aposentadoria de fração (3,25 tCO2e)
        cert = self.engine.retire_credits(
            certificate_id="CERT-FRAC-01",
            account_id="ACC-FRAC-BUYER",
            batch_id="BATCH-SOLAR-01",
            quantity_tco2e=Decimal("3.25"),
            beneficiary_name="Microempresa EcoTech",
            beneficiary_document="11222333000199",
            scope=CarbonOffsetScope.SCOPE_2,
            retirement_reason="Compensação do consumo elétrico da sede",
        )

        pos_buyer = acc_buyer.get_position("BATCH-SOLAR-01")
        self.assertEqual(pos_buyer.available_quantity, Decimal("9.50"))   # 12.75 - 3.25 = 9.50
        self.assertEqual(pos_buyer.retired_quantity, Decimal("3.25"))

        # Invariantes devem passar perfeitamente
        self.engine.assert_invariants()

    # -----------------------------------------------------------------
    # 9. DETECÇÃO ATIVA DE VIOLAÇÃO DE INVARIANTES
    # -----------------------------------------------------------------

    def test_invariants_failure_detection(self) -> None:
        """Verifica se quebras intencionais de conservação são detectadas pelas asserções formais."""
        self.engine.register_project(
            project_id="PRJ-TEST-INV",
            name="Projeto Teste Invariantes",
            standard=CertificationStandard.PADRAO_B3,
            project_type=CarbonProjectType.ARR,
            country="BR",
            state_or_province="PR",
            developer_name="Dev Test",
            registry_url_or_id="https://b3.com.br",
            methodology="M-01",
            validator_body="VVB-01",
        )
        batch = self.engine.register_batch(
            batch_id="BATCH-TEST-INV",
            project_id="PRJ-TEST-INV",
            vintage=2023,
            serial_prefix="B3-TEST",
            serial_start=1,
            serial_end=1000,
            total_issued_tco2e="1000.00",
        )
        acc = self.engine.open_custody_account(
            account_id="ACC-TEST",
            participant_code="0088",
            holder_name="Holder Test",
            holder_document="11122233344",
        )
        self.engine.deposit_initial_batch("ACC-TEST", "BATCH-TEST-INV", "1000.00")

        # Corrupção proposital 1: violação da conservação do lote (criando créditos do nada)
        batch.total_issued_tco2e = Decimal("1500.00")
        audit = self.engine.verify_invariants()
        self.assertFalse(audit["INV-VCM-001_ANTI_DOUBLE_COUNTING"])
        with self.assertRaises(CarbonInvariantViolationError):
            self.engine.assert_invariants()

        # Restaura e corrompe posições da conta
        batch.total_issued_tco2e = Decimal("1000.00")
        self.engine.assert_invariants()

        # Corrupção proposital 2: saldo negativo injetado
        pos = acc.get_position("BATCH-TEST-INV")
        object.__setattr__(pos, "available_quantity", Decimal("-50.00"))
        audit2 = self.engine.verify_invariants()
        self.assertFalse(audit2["INV-VCM-002_NON_NEGATIVE_BALANCES"])
        with self.assertRaises(CarbonInvariantViolationError):
            self.engine.assert_invariants()

    # -----------------------------------------------------------------
    # 10. SERIALIZAÇÃO E ROUNDTRIP (to_dict / from_dict)
    # -----------------------------------------------------------------

    def test_serialization_roundtrip(self) -> None:
        """Verifica a idempotência e reconstituição completa via to_dict e load_from_dict."""
        self.engine.register_project(
            project_id="PRJ-ROUNDTRIP",
            name="Projeto Roundtrip",
            standard=CertificationStandard.GOLD_STANDARD,
            project_type=CarbonProjectType.DAC,
            country="BR",
            state_or_province="MG",
            developer_name="DAC Corp",
            registry_url_or_id="https://goldstandard.org",
            methodology="GS-DAC",
            validator_body="TÜV",
        )
        self.engine.register_batch(
            batch_id="BATCH-RT-01",
            project_id="PRJ-ROUNDTRIP",
            vintage=2025,
            serial_prefix="GS-DAC-2025",
            serial_start=1,
            serial_end=2000,
            total_issued_tco2e="2000.00",
        )
        self.engine.open_custody_account(
            account_id="ACC-RT",
            participant_code="0072",
            holder_name="Holder RT",
            holder_document="12345678000195",
        )
        self.engine.deposit_initial_batch("ACC-RT", "BATCH-RT-01", "2000.00")

        # Serializa para dict e converte para JSON string
        serialized_dict = self.engine.to_dict()
        json_str = json.dumps(serialized_dict, ensure_ascii=False)
        self.assertTrue(len(json_str) > 0)

        # Novo motor reconstitui o estado
        restored_engine = VoluntaryCarbonCreditsEngine()
        restored_engine.load_from_dict(json.loads(json_str))

        self.assertIn("PRJ-ROUNDTRIP", restored_engine.projects)
        self.assertIn("BATCH-RT-01", restored_engine.batches)
        self.assertIn("ACC-RT", restored_engine.custody_accounts)
        restored_engine.assert_invariants()


if __name__ == "__main__":
    unittest.main()
