"""Suíte de Testes Automatizados para o Gerador Sintético e Motor de Replay de Mercado (B3).

Valida de ponta a ponta:
1. Reprodutibilidade determinística via sementes estocásticas (`seed`).
2. Ações (50 tickers B3, modelagem estocástica log-normal, spreads e volumes).
3. Futuros de DI (curva de juros DI1F27 a DI1F35, interpolação exponencial e PU B3).
4. Futuros de Dólar (DOL/WDO e as 4 janelas intradiárias de PTAX do BACEN).
5. Gravames SNG (chassis ISO 3779, contratos CDC/Leasing e conformidade estrita LAW-112).
6. Apólices SRO SUSEP (multi-ramo, cosseguro 100.00% e teto LMG LAW-113).
7. Exportação PUMA SBE binário com decodificação via PumaSbeCodec.
8. Exportação PUMA FIX 4.4 e FIX 5.0 SP2 com validação estrita de BodyLength e CheckSum.
9. Exportação de arquivos planos IMBARQ (004, 006, 014) validados pelos parsers canônicos.
10. Exportação de JSON institucional de estresse regulatório (Câmara CORE & BACEN).
11. Replay Engine (modos batch wire-speed, velocidades aceleradas, step e callbacks).

100% Python Standard Library: unittest, decimal, json, re, datetime.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import re
import sys
import unittest

# Garante a raiz do projeto no PYTHONPATH
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.synthetic_market_generator import (
    DIFutureContract,
    DollarContractQuote,
    EquityQuote,
    MarketReplayEvent,
    ReplayEngine,
    ReplaySpeed,
    ReplayStatus,
    SngContractData,
    SroPolicyData,
    SyntheticMarketGenerator,
)
from src.puma_sbe import (
    ExecutionReportBinary,
    NewOrderSingleBinary,
    PumaSbeCodec,
)
from src.puma_fix import FixMessage
from src.imbarq004 import Imbarq004Parser
from src.imbarq006 import Imbarq006Parser
from src.imbarq014 import Imbarq014Parser
from src.sng_gravames_engine import ModalidadeGravame, SngGravamesEngine, VeiculoIdentificacao
from src.sro_seguros_engine import CosseguradorShare, RamoSeguro, SroSegurosEngine


class TestSyntheticMarketGenerator(unittest.TestCase):
    """Bateria de testes unitários e de integração do Gerador Sintético B3."""

    def setUp(self) -> None:
        self.seed = 42
        self.generator = SyntheticMarketGenerator(seed=self.seed)

    # --------------------------------------------------------------------------
    # 1. TESTE DE REPRODUTIBILIDADE DETERMINÍSTICA
    # --------------------------------------------------------------------------
    def test_seed_reproducibility(self) -> None:
        """Garante que a mesma semente produz rigorosamente os mesmos dados estocásticos."""
        gen_a = SyntheticMarketGenerator(seed=12345)
        gen_b = SyntheticMarketGenerator(seed=12345)
        gen_c = SyntheticMarketGenerator(seed=99999)

        quotes_a = gen_a.generate_equities_step()
        quotes_b = gen_b.generate_equities_step()
        quotes_c = gen_c.generate_equities_step()

        # Mesma semente -> valores idênticos
        self.assertEqual(len(quotes_a), len(quotes_b))
        for qa, qb in zip(quotes_a, quotes_b):
            self.assertEqual(qa.ticker, qb.ticker)
            self.assertEqual(qa.price, qb.price)
            self.assertEqual(qa.volume, qb.volume)
            self.assertEqual(qa.bid, qb.bid)
            self.assertEqual(qa.ask, qb.ask)

        # Sementes diferentes -> divergência estocástica
        prices_a = [q.price for q in quotes_a]
        prices_c = [q.price for q in quotes_c]
        self.assertNotEqual(prices_a, prices_c)

        # Reset da semente
        gen_a.reset_seed(12345)
        quotes_a_reset = gen_a.generate_equities_step()
        self.assertEqual([q.price for q in quotes_b], [q.price for q in quotes_a_reset])

    # --------------------------------------------------------------------------
    # 2. TESTE DE AÇÕES (50 TICKERS B3 & LOG-NORMALIDADE)
    # --------------------------------------------------------------------------
    def test_equities_generation_50_tickers(self) -> None:
        """Valida que exatamente 50 tickers institucionais da B3 são gerados com spreads coerentes."""
        quotes = self.generator.generate_equities_step()
        self.assertEqual(len(quotes), 50, "Deve conter exatamente 50 ativos de ações")

        tickers = [q.ticker for q in quotes]
        self.assertIn("PETR4", tickers)
        self.assertIn("VALE3", tickers)
        self.assertIn("ITUB4", tickers)
        self.assertIn("B3SA3", tickers)
        self.assertIn("WEGE3", tickers)
        self.assertIn("RENT3", tickers)

        for q in quotes:
            self.assertIsInstance(q, EquityQuote)
            self.assertGreater(q.price, Decimal("0.00"), f"Preço deve ser positivo para {q.ticker}")
            self.assertGreater(q.volume, Decimal("0.00"), f"Volume deve ser positivo para {q.ticker}")
            self.assertLessEqual(q.bid, q.ask, f"Bid ({q.bid}) não pode ser maior que Ask ({q.ask}) em {q.ticker}")
            self.assertTrue(q.isin.startswith("BR"), f"ISIN brasileiro deve começar com 'BR': {q.isin}")
            self.assertEqual(len(q.isin), 12, f"ISIN deve conter exatamente 12 caracteres: {q.isin}")

    def test_equities_multi_step_gbm(self) -> None:
        """Valida a progressão temporal multi-step do movimento browniano geométrico."""
        p_petr4_init = next(q.price for q in self.generator.generate_equities_step() if q.ticker == "PETR4")
        step_prices = [p_petr4_init]

        for _ in range(5):
            quotes = self.generator.generate_equities_step()
            px = next(q.price for q in quotes if q.ticker == "PETR4")
            step_prices.append(px)

        self.assertEqual(len(step_prices), 6)
        # Todos os preços positivos e sem colapso
        for px in step_prices:
            self.assertGreater(px, Decimal("0.00"))

    # --------------------------------------------------------------------------
    # 3. TESTE DE FUTUROS DI (CURVA DI1F27 A DI1F35, PU B3 & INTERPOLAÇÃO)
    # --------------------------------------------------------------------------
    def test_di_futures_curve_and_pu(self) -> None:
        """Valida a geração dos 9 vértices da curva DI1 e a precisão do cálculo de PU B3."""
        curve = self.generator.generate_di_curve()
        self.assertEqual(len(curve), 9)

        expected_symbols = [
            "DI1F27", "DI1F28", "DI1F29", "DI1F30", "DI1F31",
            "DI1F32", "DI1F33", "DI1F34", "DI1F35"
        ]
        symbols = [c.symbol for c in curve]
        self.assertEqual(symbols, expected_symbols)

        for contract in curve:
            self.assertIsInstance(contract, DIFutureContract)
            self.assertGreater(contract.rate_annual, Decimal("0.00"))
            self.assertGreater(contract.unit_price, Decimal("0.00"))
            self.assertLessEqual(contract.unit_price, Decimal("100000.00"), "PU do DI1 é sempre descontado (<= 100k)")

            # Validação matemática independente do PU B3: PU = 100000 / (1 + r)^(du/252)
            r = float(contract.rate_annual) / 100.0
            du = contract.business_days
            expected_pu = 100_000.0 / ((1.0 + r) ** (du / 252.0))
            self.assertAlmostEqual(float(contract.unit_price), expected_pu, delta=0.5)

    def test_di_exponential_interpolation(self) -> None:
        """Valida a interpolação exponencial contínua em vértice intermediário não padronizado."""
        curve = self.generator.generate_di_curve()
        # Interpola para 400 dias úteis (entre DI1F28=292 du e DI1F29=544 du)
        rate_interp, pu_interp = self.generator.interpolate_di_rate(target_du=400, curve=curve)

        self.assertGreater(rate_interp, Decimal("11.00"))
        self.assertLess(rate_interp, Decimal("12.50"))
        self.assertGreater(pu_interp, Decimal("80000.00"))
        self.assertLess(pu_interp, Decimal("90000.00"))

        # Fator do PU interpolado deve ser consistente
        expected_pu = 100_000.0 / ((1.0 + float(rate_interp) / 100.0) ** (400.0 / 252.0))
        self.assertAlmostEqual(float(pu_interp), expected_pu, delta=0.5)

    # --------------------------------------------------------------------------
    # 4. TESTE DE FUTUROS DE DÓLAR & 4 JANELAS PTAX DO BACEN
    # --------------------------------------------------------------------------
    def test_dollar_futures_and_ptax_windows(self) -> None:
        """Valida os contratos DOL/WDO e o cálculo da PTAX com as 4 janelas do Banco Central."""
        fx = self.generator.generate_dollar_ptax(base_spot=5.7500)
        self.assertIsInstance(fx, DollarContractQuote)

        # 4 Janelas obrigatórias do BACEN
        self.assertEqual(len(fx.ptax_windows), 4)
        expected_windows = ["1ª Janela PTAX", "2ª Janela PTAX", "3ª Janela PTAX", "4ª Janela PTAX"]
        self.assertEqual([w.window_name for w in fx.ptax_windows], expected_windows)

        # Validação da média aritmética
        sum_mids = sum(w.mid for w in fx.ptax_windows)
        expected_ptax_mid = (sum_mids / Decimal("4")).quantize(Decimal("0.0001"))
        self.assertAlmostEqual(float(fx.ptax_final), float(expected_ptax_mid), places=3)

        # Preços Unitários dos contratos
        # DOL = Futuro * 50.000 USD
        self.assertEqual(fx.dol_pu, (fx.future_rate * Decimal("50000")).quantize(Decimal("0.01")))
        # WDO = Futuro * 10.000 USD
        self.assertEqual(fx.wdo_pu, (fx.future_rate * Decimal("10000")).quantize(Decimal("0.01")))

    # --------------------------------------------------------------------------
    # 5. TESTE DE GRAVAMES SNG: CHASSI ISO 3779 & INVARIANTE LAW-112
    # --------------------------------------------------------------------------
    def test_sng_iso3779_and_law_112_compliance(self) -> None:
        """Valida que todos os chassis gerados seguem a norma ISO 3779 e preservam a invariante LAW-112."""
        portfolio = self.generator.generate_sng_portfolio(count=30)
        self.assertEqual(len(portfolio), 30)

        # Regex ISO 3779: 17 caracteres maiúsculos sem I, O, Q
        iso3779_regex = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
        chassis_set = set()

        for contract in portfolio:
            self.assertIsInstance(contract, SngContractData)
            self.assertRegex(contract.chassi, iso3779_regex, f"Chassi {contract.chassi} não atende ISO 3779")
            self.assertNotIn("I", contract.chassi)
            self.assertNotIn("O", contract.chassi)
            self.assertNotIn("Q", contract.chassi)
            self.assertEqual(len(contract.chassi), 17)
            self.assertEqual(len(contract.renavam), 11)
            self.assertTrue(contract.law_112_compliant)

            # Unicidade de chassi para gravames ativos (LAW-112)
            self.assertNotIn(contract.chassi, chassis_set, f"Duplicidade violaria LAW-112: {contract.chassi}")
            chassis_set.add(contract.chassi)

    def test_sng_law_112_violation_prevention(self) -> None:
        """Valida que o motor rejeita terminantemente inserção de gravame concorrente para mesmo chassi ativo."""
        engine = SngGravamesEngine()
        valid_chassi = "9BD172635TBP44556"
        veiculo = VeiculoIdentificacao(
            chassi=valid_chassi,
            renavam="12345678901",
            placa="ABC1D23",
            uf_emplacamento="SP",
            marca_modelo="FIAT STRADA",
            ano_fabricacao=2025,
            ano_modelo=2026,
        )

        # 1. Primeiro gravame: sucesso
        engine.registrar_gravame(
            contrato_numero="CTR-001",
            modalidade=ModalidadeGravame.ALIENACAO_FIDUCIARIA,
            credor_cnpj="60746948000112",
            credor_nome="BANCO A",
            devedor_documento="12345678909",
            devedor_nome="JOAO DA SILVA",
            veiculo=veiculo,
            valor_financiamento=Decimal("50000.00"),
            quantidade_parcelas=48,
            taxa_juros_anual_pct=Decimal("15.50"),
        )

        # 2. Tentativa de segundo gravame concorrente no mesmo chassi DEVE falhar (LAW-112)
        with self.assertRaises(ValueError) as ctx:
            engine.registrar_gravame(
                contrato_numero="CTR-002",
                modalidade=ModalidadeGravame.RESERVA_DOMINIO,
                credor_cnpj="99999999000199",
                credor_nome="BANCO B",
                devedor_documento="98765432100",
                devedor_nome="MARIA SANTOS",
                veiculo=veiculo,
                valor_financiamento=Decimal("60000.00"),
                quantidade_parcelas=36,
                taxa_juros_anual_pct=Decimal("16.00"),
            )
        self.assertIn("LAW-112", str(ctx.exception))

    # --------------------------------------------------------------------------
    # 6. TESTE DE APÓLICES SRO SUSEP & INVARIANTE LAW-113 (LMG)
    # --------------------------------------------------------------------------
    def test_sro_policies_law_113_compliance(self) -> None:
        """Valida que contratos SRO cobrem multi-ramos, quotas de cosseguro somam 100% e respeitam teto LMG."""
        policies = self.generator.generate_sro_portfolio(count=16, simulate_claims=True)
        self.assertEqual(len(policies), 16)

        ramos_presentes = {p.ramo for p in policies}
        self.assertIn(RamoSeguro.AUTOMOVEL, ramos_presentes)
        self.assertIn(RamoSeguro.PATRIMONIAL_COMPREENSIVO, ramos_presentes)
        self.assertIn(RamoSeguro.RURAL_AGRICOLA, ramos_presentes)
        self.assertIn(RamoSeguro.VIDA_INDIVIDUAL_COLETIVO, ramos_presentes)

        for p in policies:
            self.assertIsInstance(p, SroPolicyData)
            # Conservação de Cosseguro: soma exata de 100.00% (LAW-113)
            sum_quotas = sum(c.percentual_quota for c in p.cosseguradores)
            self.assertEqual(sum_quotas, Decimal("100.00"), f"Quotas devem somar 100%: apólice {p.apolice_id}")

            # Teto LMG: total sinistrado não pode exceder o LMG (LAW-113)
            self.assertLessEqual(p.total_sinistrado, p.limite_maximo_garantia)
            self.assertTrue(p.law_113_compliant)

    def test_sro_law_113_violation_prevention(self) -> None:
        """Valida que tentativa de liquidar sinistro acima do LMG é barrada com LAW-113."""
        engine = SroSegurosEngine()
        lmg = Decimal("100000.00")
        apolice = engine.registrar_apolice(
            numero_proposta="PROP-TEST-001",
            ramo=RamoSeguro.AUTOMOVEL,
            seguradora_lider_cnpj="33000118000179",
            seguradora_lider_nome="BRADESCO AUTO/RE",
            segurado_documento="11122233344",
            segurado_nome="CLIENTE TESTE",
            data_emissao="2026-11-03",
            inicio_vigencia="2026-11-03",
            fim_vigencia="2027-11-03",
            limite_maximo_garantia=lmg,
            premio_liquido=Decimal("2500.00"),
        )

        sin = engine.avisar_sinistro(
            apolice_id=apolice.apolice_id,
            data_ocorrencia="2026-11-05",
            valor_estimado=Decimal("150000.00"),
        )

        # Tentativa de liquidação acima de 100.000 LMG DEVE falhar (LAW-113)
        with self.assertRaises(ValueError) as ctx:
            engine.liquidar_sinistro(
                apolice_id=apolice.apolice_id,
                sinistro_id=sin.sinistro_id,
                valor_indenizado=Decimal("150000.00"),
            )
        self.assertIn("LAW-113", str(ctx.exception))

    # --------------------------------------------------------------------------
    # 7. TESTE DE EXPORTAÇÃO PUMA SBE BINÁRIO
    # --------------------------------------------------------------------------
    def test_export_puma_sbe_binary_decodable(self) -> None:
        """Valida que as mensagens binárias geradas no padrão SBE são decodificáveis pelo PumaSbeCodec."""
        packets = self.generator.export_puma_sbe(orders_count=10)
        self.assertGreater(len(packets), 10)

        for pkt in packets:
            self.assertIsInstance(pkt, bytes)
            decoded = PumaSbeCodec.decode(pkt)
            self.assertIn(type(decoded), (NewOrderSingleBinary, ExecutionReportBinary))
            if isinstance(decoded, NewOrderSingleBinary):
                self.assertGreater(decoded.cl_ord_id, 0)
                self.assertGreater(decoded.price, 0.0)
                self.assertGreater(decoded.order_qty, 0)
            elif isinstance(decoded, ExecutionReportBinary):
                self.assertGreater(decoded.order_id, 0)
                self.assertGreater(decoded.last_px, 0.0)

    # --------------------------------------------------------------------------
    # 8. TESTE DE EXPORTAÇÃO FIX 4.4 & FIX 5.0 SP2
    # --------------------------------------------------------------------------
    def test_export_puma_fix_messages(self) -> None:
        """Valida que mensagens FIX 4.4 e FIX 5.0 SP2 possuem CheckSum e BodyLength matematicamente corretos."""
        # 1. FIX 4.4
        fix44_logs = self.generator.export_puma_fix(orders_count=6, version="FIX.4.4", delimiter="|")
        self.assertEqual(len(fix44_logs), 12)  # 6 NewOrderSingle (35=D) + 6 ExecutionReport (35=8)

        for line in fix44_logs:
            parsed = FixMessage.decode(line, delimiter=b"|", validate_integrity=True)
            self.assertEqual(parsed.begin_string, "FIX.4.4")
            self.assertIn(parsed.msg_type, ("D", "8"))
            self.assertTrue(parsed.has(10))
            self.assertTrue(parsed.has(9))

        # 2. FIX 5.0 SP2
        fix50_logs = self.generator.export_puma_fix(orders_count=4, version="FIX.5.0SP2", delimiter="|")
        self.assertEqual(len(fix50_logs), 8)

        for line in fix50_logs:
            parsed = FixMessage.decode(line, delimiter=b"|", validate_integrity=True)
            self.assertEqual(parsed.begin_string, "FIX.5.0SP2")
            self.assertIn(parsed.msg_type, ("D", "8"))

    # --------------------------------------------------------------------------
    # 9. TESTE DE ARQUIVOS PLANOS IMBARQ (004, 006, 014)
    # --------------------------------------------------------------------------
    def test_export_imbarq004_parsing(self) -> None:
        """Garante que o arquivo IMBARQ004 atende ao layout de 200 colunas e passa no parser canônico."""
        raw_content = self.generator.export_imbarq004(date_str="20261103", items_count=8)
        lines = [line for line in raw_content.splitlines() if line]

        for i, line in enumerate(lines, start=1):
            self.assertEqual(len(line), 200, f"Linha {i} do IMBARQ004 deve ter 200 colunas")

        # Parsing canônico com checagem rigorosa de trailer e lançamentos
        parsed = Imbarq004Parser().parse_lines(lines)
        self.assertEqual(parsed.header.identificador_layout, "IMBARQ004")
        self.assertEqual(len(parsed.detalhes), 8)
        self.assertEqual(parsed.trailer.total_registros_detalhe, 8)
        self.assertEqual(parsed.trailer.total_registros_arquivo, 10)

    def test_export_imbarq006_parsing(self) -> None:
        """Garante que o arquivo IMBARQ006 atende ao layout de 200 colunas e passa no parser canônico."""
        raw_content = self.generator.export_imbarq006(date_str="20261103", items_count=6)
        lines = [line for line in raw_content.splitlines() if line]

        for i, line in enumerate(lines, start=1):
            self.assertEqual(len(line), 200, f"Linha {i} do IMBARQ006 deve ter 200 colunas")

        parsed = Imbarq006Parser().parse_lines(lines)
        self.assertEqual(parsed.header.identificador_layout, "IMBARQ006")
        self.assertEqual(len(parsed.detalhes), 6)
        self.assertEqual(parsed.trailer.total_registros_detalhe, 6)
        self.assertEqual(parsed.trailer.total_registros_arquivo, 8)

    def test_export_imbarq014_parsing(self) -> None:
        """Garante que o arquivo IMBARQ014 atende ao layout de 200 colunas e passa no parser canônico."""
        raw_content = self.generator.export_imbarq014(date_str="20261103", items_count=6)
        lines = [line for line in raw_content.splitlines() if line]

        for i, line in enumerate(lines, start=1):
            self.assertEqual(len(line), 200, f"Linha {i} do IMBARQ014 deve ter 200 colunas")

        parsed = Imbarq014Parser().parse_lines(lines)
        self.assertEqual(parsed.header.identificador_layout, "IMBARQ014")
        self.assertEqual(len(parsed.detalhes), 6)
        self.assertEqual(parsed.trailer.total_registros_detalhe, 6)
        self.assertEqual(parsed.trailer.total_registros_arquivo, 8)

    # --------------------------------------------------------------------------
    # 10. TESTE DE JSON INSTITUCIONAL DE ESTRESSE
    # --------------------------------------------------------------------------
    def test_export_stress_json(self) -> None:
        """Valida que o payload JSON de estresse contém todos os blocos institucionais e leis formais."""
        raw_json = self.generator.export_stress_json(
            scenario_name="TEST_SCENARIO_COVID",
            equity_drop_pct=-25.0,
            vol_multiplier=4.0,
            di_curve_shift_bps=300.0,
            fx_spike_pct=15.0,
        )
        data = json.loads(raw_json)

        self.assertIn("stress_scenario", data)
        self.assertIn("market_data", data)
        self.assertIn("collateral_and_insurance", data)

        scenario = data["stress_scenario"]
        self.assertEqual(scenario["scenario_name"], "TEST_SCENARIO_COVID")
        self.assertEqual(scenario["simulation_seed"], 42)

        market = data["market_data"]
        self.assertEqual(market["equities_count"], 50)
        self.assertEqual(len(market["di_futures_curve"]), 9)
        self.assertEqual(len(market["fx_ptax"]["ptax_windows"]), 4)

        gov = data["collateral_and_insurance"]
        self.assertTrue(gov["sng_law_112_verified"])
        self.assertTrue(gov["sro_law_113_verified"])

    # --------------------------------------------------------------------------
    # 11. TESTE DO REPLAY ENGINE
    # --------------------------------------------------------------------------
    def test_replay_engine_batch_wire_speed(self) -> None:
        """Valida a execução em lote (batch wire-speed) processando centenas de eventos em microssegundos."""
        engine = ReplayEngine(speed=ReplaySpeed.BATCH_WIRE_SPEED)
        events_received: List[MarketReplayEvent] = []

        engine.subscribe(lambda evt: events_received.append(evt))
        engine.load_from_generator(self.generator, steps=3)

        self.assertGreater(len(engine.events_queue), 100)
        stats = engine.play()

        self.assertEqual(stats["status"], ReplayStatus.COMPLETED.value)
        self.assertEqual(stats["events_processed"], len(events_received))
        self.assertGreater(stats["throughput_events_per_second"], 10_000.0)
        self.assertIn("EQUITY_QUOTE_TICK", stats["events_by_type"])
        self.assertIn("DI_CURVE_UPDATE", stats["events_by_type"])
        self.assertIn("SNG_GRAVAME_INCLUSAO", stats["events_by_type"])
        self.assertIn("SRO_APOLICE_REGISTRO", stats["events_by_type"])

    def test_replay_engine_step_and_pause(self) -> None:
        """Valida execução passo a passo (step) e controles de pausa do Replay Engine."""
        engine = ReplayEngine(speed=ReplaySpeed.BATCH_WIRE_SPEED)
        engine.load_from_generator(self.generator, steps=1)

        # 1. Step individual
        evt1 = engine.step()
        self.assertIsNotNone(evt1)
        self.assertEqual(engine.events_processed, 1)

        evt2 = engine.step()
        self.assertIsNotNone(evt2)
        self.assertEqual(engine.events_processed, 2)
        self.assertNotEqual(evt1.sequence_id, evt2.sequence_id)

        # 2. Pausa e retomada
        engine.pause()
        self.assertEqual(engine.status, ReplayStatus.PAUSED)
        engine.resume()
        self.assertEqual(engine.status, ReplayStatus.PLAYING)

        # 3. Interrupção (stop)
        engine.stop()
        self.assertEqual(engine.status, ReplayStatus.STOPPED)

    def test_replay_engine_speed_multiplier(self) -> None:
        """Valida a execução com multiplicador acelerado (100x)."""
        engine = ReplayEngine(speed=ReplaySpeed.FAST_100X)
        engine.load_from_generator(self.generator, steps=1)
        stats = engine.play()
        self.assertEqual(stats["status"], ReplayStatus.COMPLETED.value)
        self.assertEqual(stats["events_processed"], len(engine.events_queue))
        self.assertGreater(stats["throughput_events_per_second"], 0.0)

    def test_replay_engine_event_type_subscription(self) -> None:
        """Valida a assinatura de callbacks específicos por tipo de evento."""
        engine = ReplayEngine(speed=ReplaySpeed.BATCH_WIRE_SPEED)
        di_events: List[MarketReplayEvent] = []

        # Inscreve-se apenas em eventos de curva DI
        engine.subscribe(lambda evt: di_events.append(evt), event_type="DI_CURVE_UPDATE")
        engine.load_from_generator(self.generator, steps=2)
        engine.play()

        self.assertEqual(len(di_events), 9)
        for evt in di_events:
            self.assertEqual(evt.event_type, "DI_CURVE_UPDATE")


if __name__ == "__main__":
    unittest.main()
