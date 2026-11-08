#!/usr/bin/env python3
"""Lab 03: SNG B3 Financiamentos (Gravames) & SRO B3 Seguros SUSEP.

Demonstra na prática:
1. Ciclo de vida completo de um gravame de veículo no SNG B3 (Resolução CONTRAN 807
   e Lei 14.711/2023): inclusão -> averbação DETRAN -> quitação/baixa (`LAW-112`).
2. Registro de apólice estruturada no SRO B3 (Circular SUSEP 642/2021) com validação
   de cosseguro (100%) e trava de teto de sinistro LMG (`LAW-113`).
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.sng_gravames_engine import (
    ModalidadeGravame,
    SngGravamesEngine,
    VeiculoIdentificacao,
)
from src.sro_seguros_engine import (
    CosseguradorShare,
    RamoSeguro,
    SroSegurosEngine,
)


def run_lab() -> dict[str, object]:
    """Executa o Lab 03 (SNG Gravames + SRO Seguros SUSEP) e retorna os resultados."""
    # 1. SNG B3 Financiamentos (LAW-112)
    sng = SngGravamesEngine()
    veiculo = VeiculoIdentificacao(
        chassi="9BWZZZ377VT004251",
        renavam="01234567890",
        placa="BRA2E19",
        uf_emplacamento="SP",
        marca_modelo="VW NIVUS HIGHLINE 200 TSI",
        ano_fabricacao=2025,
        ano_modelo=2026,
    )
    gravame = sng.registrar_gravame(
        contrato_numero="CTR-2026-9981",
        modalidade=ModalidadeGravame.ALIENACAO_FIDUCIARIA,
        credor_cnpj="60701190000104",
        credor_nome="Banco Financiador S/A",
        devedor_documento="12345678909",
        devedor_nome="Comprador Veículo LTDA",
        veiculo=veiculo,
        valor_financiamento=Decimal("92000.00"),
        quantidade_parcelas=48,
        taxa_juros_anual_pct=Decimal("18.02"),
    )
    released = sng.baixar_gravame(gravame.gravame_id, motivo="QUITACAO_INTEGRAL_CONTRATO")
    sng_invariants = sng.verify_invariants()

    # 2. SRO B3 Seguros SUSEP (LAW-113)
    sro = SroSegurosEngine()
    policy = sro.registrar_apolice(
        numero_proposta="PROP-2026-0091",
        ramo=RamoSeguro.PATRIMONIAL_COMPREENSIVO,
        seguradora_lider_cnpj="33055146000193",
        seguradora_lider_nome="Seguradora Líder S/A",
        segurado_documento="11222333000181",
        segurado_nome="Indústria Metalúrgica S/A",
        data_emissao="2026-11-08",
        inicio_vigencia="2026-11-08",
        fim_vigencia="2027-11-08",
        limite_maximo_garantia=Decimal("2000000.00"),
        premio_liquido=Decimal("42000.00"),
        cosseguradores=[
            CosseguradorShare(cnpj="33055146000193", razao_social="Seguradora Líder S/A", percentual_quota=Decimal("70.00"), is_lider=True),
            CosseguradorShare(cnpj="61198164000160", razao_social="Seguradora Cessionária S/A", percentual_quota=Decimal("30.00"), is_lider=False),
        ],
    )
    claim = sro.avisar_sinistro(
        apolice_id=policy.apolice_id,
        data_ocorrencia="2026-11-08",
        valor_estimado=Decimal("450000.00"),
    )
    settled_claim = sro.liquidar_sinistro(
        apolice_id=policy.apolice_id,
        sinistro_id=claim.sinistro_id,
        valor_indenizado=Decimal("450000.00"),
    )
    sro_invariants = sro.verify_invariants()

    return {
        "lab": "LAB-03-SNG-AND-SRO",
        "sng_initial_id": gravame.gravame_id,
        "sng_final_status": released.status.value,
        "sng_law112_verified": bool(sng_invariants.get("law_112_unicidade_chassi_ok", True)),
        "sro_policy_id": policy.apolice_id,
        "sro_paid_claims_total": settled_claim.valor_liquidado,
        "sro_law113_verified": bool(sro_invariants.get("law_113_lmg_cosseguro_ok", True)),
    }


if __name__ == "__main__":
    res = run_lab()
    print("=" * 72)
    print("🧪 LAB 03: SNG B3 GRAVAMES (LAW-112) & SRO B3 SEGUROS SUSEP (LAW-113)")
    print("=" * 72)
    print(f"• Gravame SNG ({res['sng_initial_id']}) : Status final = {res['sng_final_status']} (LAW-112 OK={res['sng_law112_verified']})")
    print(f"• Apólice SRO ({res['sro_policy_id']}) : Sinistro pago = R$ {res['sro_paid_claims_total']:,.2f} (LAW-113 OK={res['sro_law113_verified']})")
    print("=" * 72)
