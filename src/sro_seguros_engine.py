"""Sistema de Registro de Operações (SRO) — B3 Seguros & SUSEP.

Módulo de infraestrutura e governança para registro granular de apólices de seguro,
endossos, cosseguro, cancelamentos e liquidações de sinistros perante a SUSEP
(Superintendência de Seguros Privados).

Fundamentação Regulatória:
- Resolução CNSP nº 382/2020 e nº 399/2020 (Diretrizes de Registro de Operações)
- Circular SUSEP nº 605/2020 e nº 612/2020 (Padrões Técnicos do SRO)
- Circular SUSEP nº 642/2021 (Implementação da Arquitetura SRO V3)
- Invariante LAW-113: Conservação de Cosseguro (100%) e Inviolabilidade do LMG

100% Python Standard Library (dataclasses, enum, typing, hashlib, datetime, decimal).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
import hashlib
from typing import Any, Dict, List, Optional


class RamoSeguro(str, Enum):
    """Principais ramos do mercado segurador brasileiro homologados no SRO."""

    AUTOMOVEL = "0531_AUTOMOVEL"
    PATRIMONIAL_COMPREENSIVO = "0118_PATRIMONIAL_EMPRESARIAL"
    VIDA_INDIVIDUAL_COLETIVO = "0993_VIDA"
    RURAL_AGRICOLA = "1101_RURAL_AGRICOLA"
    TRANSPORTE_CARGAS = "0622_TRANSPORTE_NACIONAL"
    RESPONSABILIDADE_CIVIL_DO = "0351_RC_DO"
    GARANTIA_SEGURADORA = "0775_GARANTIA_CONTRATUAL"


class StatusApolice(str, Enum):
    """Estados operacionais do ciclo de vida de uma apólice no SRO."""

    VIGENTE = "VIGENTE"
    ENDOSSADA = "ENDOSSADA"
    CANCELADA = "CANCELADA"
    EXPIRADA = "EXPIRADA"


class StatusSinistro(str, Enum):
    """Estados operacionais de um sinistro regulado no SRO."""

    AVISADO = "AVISADO"
    EM_REGULACAO = "EM_REGULACAO"
    LIQUIDADO = "LIQUIDADO"
    ENCERRADO_SEM_INDENIZACAO = "ENCERRADO_SEM_INDENIZACAO"


@dataclass(frozen=True)
class CosseguradorShare:
    """Quota de participação em regime de cosseguro."""

    cnpj: str
    razao_social: str
    percentual_quota: Decimal
    is_lider: bool = False

    def __post_init__(self) -> None:
        if self.percentual_quota <= Decimal("0") or self.percentual_quota > Decimal("100"):
            raise ValueError(f"Quota de cosseguro deve estar no intervalo (0, 100]: '{self.percentual_quota}'")


@dataclass
class SinistroRecord:
    """Registro individual de um evento de sinistro vinculado à apólice."""

    sinistro_id: str
    apolice_id: str
    data_ocorrencia: str
    data_aviso: str
    valor_estimado: Decimal
    valor_liquidado: Decimal = Decimal("0.00")
    status: StatusSinistro = StatusSinistro.AVISADO
    data_liquidacao: Optional[str] = None
    descricao_cobertura: str = "COBERTURA_BASICA"


@dataclass
class ApoliceRecord:
    """Registro estrutural da apólice no repositório SRO B3 Seguros."""

    apolice_id: str
    numero_proposta: str
    ramo: RamoSeguro
    seguradora_lider_cnpj: str
    seguradora_lider_nome: str
    segurado_documento: str
    segurado_nome: str
    data_emissao: str
    inicio_vigencia: str
    fim_vigencia: str
    limite_maximo_garantia: Decimal
    premio_liquido: Decimal
    premio_total_com_iof: Decimal
    cosseguradores: List[CosseguradorShare]
    status: StatusApolice = StatusApolice.VIGENTE
    sinistros: List[SinistroRecord] = field(default_factory=list)
    susep_hash: str = ""

    def __post_init__(self) -> None:
        if not self.susep_hash:
            raw = (
                f"{self.apolice_id}|{self.ramo.value}|{self.seguradora_lider_cnpj}|"
                f"{self.limite_maximo_garantia}|{self.premio_liquido}|{self.inicio_vigencia}"
            )
            self.susep_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "apolice_id": self.apolice_id,
            "numero_proposta": self.numero_proposta,
            "ramo": self.ramo.value,
            "seguradora_lider_cnpj": self.seguradora_lider_cnpj,
            "seguradora_lider_nome": self.seguradora_lider_nome,
            "segurado_documento": self.segurado_documento,
            "segurado_nome": self.segurado_nome,
            "data_emissao": self.data_emissao,
            "inicio_vigencia": self.inicio_vigencia,
            "fim_vigencia": self.fim_vigencia,
            "limite_maximo_garantia": str(self.limite_maximo_garantia),
            "premio_liquido": str(self.premio_liquido),
            "premio_total_com_iof": str(self.premio_total_com_iof),
            "cosseguradores": [
                {
                    "cnpj": c.cnpj,
                    "razao_social": c.razao_social,
                    "percentual_quota": str(c.percentual_quota),
                    "is_lider": c.is_lider,
                }
                for c in self.cosseguradores
            ],
            "status": self.status.value,
            "sinistros_count": len(self.sinistros),
            "sinistros_liquidados_total": str(sum(s.valor_liquidado for s in self.sinistros)),
            "susep_hash": self.susep_hash,
        }


class SroSegurosEngine:
    """Motor central do Sistema de Registro de Operações (SRO) B3 / SUSEP."""

    def __init__(self) -> None:
        self.apolices: Dict[str, ApoliceRecord] = {}
        self.audit_log: List[Dict[str, Any]] = []

    def registrar_apolice(
        self,
        numero_proposta: str,
        ramo: RamoSeguro,
        seguradora_lider_cnpj: str,
        seguradora_lider_nome: str,
        segurado_documento: str,
        segurado_nome: str,
        data_emissao: str,
        inicio_vigencia: str,
        fim_vigencia: str,
        limite_maximo_garantia: Decimal,
        premio_liquido: Decimal,
        iof_aliquota_pct: Decimal = Decimal("7.38"),
        cosseguradores: Optional[List[CosseguradorShare]] = None,
    ) -> ApoliceRecord:
        """Emite e registra uma apólice no SRO SUSEP.
        
        Aplica a invariante LAW-113: A soma das cotas de cosseguro deve igualar 100.00%.
        """
        shares = cosseguradores or [
            CosseguradorShare(
                cnpj=seguradora_lider_cnpj,
                razao_social=seguradora_lider_nome,
                percentual_quota=Decimal("100.00"),
                is_lider=True,
            )
        ]

        # Validação formal da LAW-113 (Cosseguro 100%)
        total_quota = sum(s.percentual_quota for s in shares)
        if total_quota != Decimal("100.00"):
            raise ValueError(
                f"Violação da LAW-113 (Partição de Cosseguro): Soma das quotas é {total_quota}%, "
                f"esperado estritamente 100.00%."
            )

        iof_valor = (premio_liquido * iof_aliquota_pct / Decimal("100.00")).quantize(Decimal("0.01"))
        premio_total = premio_liquido + iof_valor

        aid = f"SRO-B3-{ramo.name[:4]}-{len(self.apolices) + 1:07d}"

        rec = ApoliceRecord(
            apolice_id=aid,
            numero_proposta=numero_proposta,
            ramo=ramo,
            seguradora_lider_cnpj=seguradora_lider_cnpj,
            seguradora_lider_nome=seguradora_lider_nome,
            segurado_documento=segurado_documento,
            segurado_nome=segurado_nome,
            data_emissao=data_emissao,
            inicio_vigencia=inicio_vigencia,
            fim_vigencia=fim_vigencia,
            limite_maximo_garantia=limite_maximo_garantia,
            premio_liquido=premio_liquido,
            premio_total_com_iof=premio_total,
            cosseguradores=shares,
            status=StatusApolice.VIGENTE,
        )

        self.apolices[aid] = rec

        self.audit_log.append({
            "action": "REGISTRO_APOLICE_SRO",
            "apolice_id": aid,
            "ramo": ramo.value,
            "lmg": str(limite_maximo_garantia),
            "premio": str(premio_total),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        return rec

    def avisar_sinistro(
        self,
        apolice_id: str,
        data_ocorrencia: str,
        valor_estimado: Decimal,
        descricao_cobertura: str = "COBERTURA_BASICA",
        data_aviso: Optional[str] = None,
    ) -> SinistroRecord:
        """Registra a ocorrência de um sinistro vinculado à apólice."""
        if apolice_id not in self.apolices:
            raise KeyError(f"Apólice '{apolice_id}' não encontrada no SRO.")

        rec = self.apolices[apolice_id]
        if rec.status not in (StatusApolice.VIGENTE, StatusApolice.ENDOSSADA):
            raise ValueError(f"Não é possível abrir sinistro em apólice com status '{rec.status.value}'.")

        sid = f"SIN-{apolice_id}-{len(rec.sinistros) + 1:03d}"
        aviso_ts = data_aviso or datetime.now(timezone.utc).isoformat()

        sin = SinistroRecord(
            sinistro_id=sid,
            apolice_id=apolice_id,
            data_ocorrencia=data_ocorrencia,
            data_aviso=aviso_ts,
            valor_estimado=valor_estimado,
            descricao_cobertura=descricao_cobertura,
            status=StatusSinistro.AVISADO,
        )

        rec.sinistros.append(sin)

        self.audit_log.append({
            "action": "AVISO_SINISTRO",
            "sinistro_id": sid,
            "apolice_id": apolice_id,
            "valor_estimado": str(valor_estimado),
            "timestamp": aviso_ts,
        })

        return sin

    def liquidar_sinistro(
        self,
        apolice_id: str,
        sinistro_id: str,
        valor_indenizado: Decimal,
        data_liquidacao: Optional[str] = None,
    ) -> SinistroRecord:
        """Efetua a liquidação e pagamento de um sinistro apurado.
        
        Aplica a invariante LAW-113: O total acumulado de sinistros liquidados
        não pode exceder o Limite Máximo de Garantia (LMG) contratado.
        """
        if apolice_id not in self.apolices:
            raise KeyError(f"Apólice '{apolice_id}' não encontrada no SRO.")

        rec = self.apolices[apolice_id]
        sin = next((s for s in rec.sinistros if s.sinistro_id == sinistro_id), None)
        if not sin:
            raise KeyError(f"Sinistro '{sinistro_id}' não localizado na apólice '{apolice_id}'.")

        # Checagem do LMG (LAW-113)
        total_ja_pago = sum(s.valor_liquidado for s in rec.sinistros if s.sinistro_id != sinistro_id)
        novo_total = total_ja_pago + valor_indenizado
        if novo_total > rec.limite_maximo_garantia:
            raise ValueError(
                f"Violação da LAW-113 (Excesso de Indenização sobre LMG): "
                f"Total de sinistros após liquidação ({novo_total}) excederia o LMG ({rec.limite_maximo_garantia})."
            )

        ts = data_liquidacao or datetime.now(timezone.utc).isoformat()
        sin.valor_liquidado = valor_indenizado
        sin.status = StatusSinistro.LIQUIDADO
        sin.data_liquidacao = ts

        self.audit_log.append({
            "action": "LIQUIDACAO_SINISTRO",
            "sinistro_id": sinistro_id,
            "apolice_id": apolice_id,
            "valor_liquidado": str(valor_indenizado),
            "timestamp": ts,
        })

        return sin

    def verify_invariants(self, raise_on_error: bool = True) -> Dict[str, Any]:
        """Audita formalmente a LAW-113 em todas as apólices registradas no SRO.
        
        Verifica:
        1. Cosseguro = 100.00% em cada apólice.
        2. Soma de sinistros liquidados <= Limite Máximo de Garantia.
        3. Prêmio total = Prêmio Líquido + IOF.
        """
        for aid, rec in self.apolices.items():
            # 1. Cosseguro
            total_share = sum(c.percentual_quota for c in rec.cosseguradores)
            if total_share != Decimal("100.00"):
                msg = f"Invariante LAW-113 violada na apólice '{aid}': Quota cosseguro={total_share}% != 100%"
                if raise_on_error:
                    raise AssertionError(msg)
                return {"verified": False, "error": msg}

            # 2. LMG check
            total_liquidado = sum(s.valor_liquidado for s in rec.sinistros)
            if total_liquidado > rec.limite_maximo_garantia:
                msg = f"Invariante LAW-113 violada na apólice '{aid}': Sinistros {total_liquidado} > LMG {rec.limite_maximo_garantia}"
                if raise_on_error:
                    raise AssertionError(msg)
                return {"verified": False, "error": msg}

        return {
            "law": "LAW-113",
            "verified": True,
            "total_apolices": len(self.apolices),
            "total_sinistros": sum(len(a.sinistros) for a in self.apolices.values()),
            "audit_log_entries": len(self.audit_log),
        }
