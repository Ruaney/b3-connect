"""Motor de conciliação de custódia e livro de posições da câmara B3.

Este módulo realiza o batimento determinístico entre os registros de posições
em aberto reportados pela B3 (via arquivo IMBARQ006) e a base / livro interno de
custódia de uma instituição participante, identificando matches perfeitos e quebras.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from src.imbarq006 import Imbarq006File, PositionDetailRecord, parse_imbarq006


class BreakType(str, Enum):
    """Classificação determinística das quebras/divergências de conciliação."""

    QUANTITY_MISMATCH = "QUANTITY_MISMATCH"
    GHOST_IN_B3 = "GHOST_IN_B3"
    MISSING_IN_B3 = "MISSING_IN_B3"


@dataclass(frozen=True)
class InternalPosition:
    """Representação padronizada de uma posição em aberto no livro interno de custódia."""

    codigo_conta_custodia: int
    codigo_negociacao: str
    quantidade_liquida: int
    sentido_posicao: Optional[str] = None
    codigo_isin: Optional[str] = None
    tipo_mercado: Optional[str] = None
    preco_fechamento_ajuste: Optional[Any] = None
    valor_financeiro: Optional[Any] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> InternalPosition:
        """Converte dicionário flexível em uma instância tipada de InternalPosition."""
        conta = data.get("codigo_conta_custodia") or data.get("conta") or data.get("account")
        ticker = data.get("codigo_negociacao") or data.get("ticker") or data.get("symbol")
        qtd = (
            data.get("quantidade_liquida")
            if "quantidade_liquida" in data
            else (
                data.get("quantidade")
                if "quantidade" in data
                else (data.get("qty") if "qty" in data else data.get("qtd"))
            )
        )
        sentido = data.get("sentido_posicao") or data.get("sentido") or data.get("side")

        if conta is None:
            raise ValueError(f"Campo de conta obrigatório ausente no registro interno: {data}")
        if ticker is None:
            raise ValueError(f"Campo de ticker obrigatório ausente no registro interno: {data}")
        if qtd is None:
            raise ValueError(f"Campo de quantidade obrigatório ausente no registro interno: {data}")

        return cls(
            codigo_conta_custodia=int(conta),
            codigo_negociacao=str(ticker).strip().upper(),
            quantidade_liquida=int(qtd),
            sentido_posicao=str(sentido).strip().upper() if sentido else None,
            codigo_isin=str(data["codigo_isin"]).strip() if data.get("codigo_isin") else None,
            tipo_mercado=str(data["tipo_mercado"]).strip().upper() if data.get("tipo_mercado") else None,
            preco_fechamento_ajuste=data.get("preco_fechamento_ajuste"),
            valor_financeiro=data.get("valor_financeiro"),
        )


@dataclass(frozen=True)
class MatchedPosition:
    """Posição perfeitamente conciliada (match de conta, ticker e quantidade líquida)."""

    codigo_conta_custodia: int
    codigo_negociacao: str
    quantidade_liquida: int
    b3_position: PositionDetailRecord
    internal_position: InternalPosition


@dataclass(frozen=True)
class ReconciliationBreak:
    """Registro de divergência ou inconsistência apontada no processo de conciliação."""

    break_type: BreakType
    codigo_conta_custodia: int
    codigo_negociacao: str
    quantidade_b3: Optional[int]
    quantidade_interna: Optional[int]
    diferenca: int
    b3_position: Optional[PositionDetailRecord] = None
    internal_position: Optional[InternalPosition] = None
    detalhes: str = ""


@dataclass(frozen=True)
class ReconciliationReport:
    """Relatório estruturado da execução de conciliação entre B3 e livro interno."""

    data_movimento: Optional[date]
    total_posicoes_b3: int
    total_posicoes_internas: int
    matches: List[MatchedPosition] = field(default_factory=list)
    breaks: List[ReconciliationBreak] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        """Indica se a conciliação foi 100% batida sem nenhuma quebra."""
        return len(self.breaks) == 0

    @property
    def total_matches(self) -> int:
        """Quantidade total de posições perfeitamente conciliadas."""
        return len(self.matches)

    @property
    def total_breaks(self) -> int:
        """Quantidade total de quebras identificadas."""
        return len(self.breaks)

    @property
    def quantity_mismatches(self) -> List[ReconciliationBreak]:
        """Filtra quebras por divergência de quantidade."""
        return [b for b in self.breaks if b.break_type == BreakType.QUANTITY_MISMATCH]

    @property
    def ghost_positions(self) -> List[ReconciliationBreak]:
        """Filtra posições fantasmas na B3 (não encontradas no livro interno)."""
        return [b for b in self.breaks if b.break_type == BreakType.GHOST_IN_B3]

    @property
    def missing_positions(self) -> List[ReconciliationBreak]:
        """Filtra posições faltantes na B3 (presentes no livro interno mas ausentes na câmara)."""
        return [b for b in self.breaks if b.break_type == BreakType.MISSING_IN_B3]

    def summary(self) -> str:
        """Gera um relatório textual determinístico para auditoria e logs operacionais."""
        status = "100% CONCILIADO (SEM QUEBRAS)" if self.is_clean else "COM QUEBRAS DE CONCILIAÇÃO"
        dt_str = self.data_movimento.isoformat() if self.data_movimento else "N/A"

        lines = [
            "=" * 78,
            f"RELATÓRIO DE CONCILIAÇÃO DE CUSTÓDIA B3 — DATA-BASE: {dt_str}",
            f"STATUS: {status}",
            "-" * 78,
            f"Total Posições B3 (Câmara):    {self.total_posicoes_b3}",
            f"Total Posições Livro Interno: {self.total_posicoes_internas}",
            f"Posições Conciliadas (Match): {self.total_matches}",
            f"Total de Quebras Detectadas:  {self.total_breaks}",
            f"  - Divergência de Quantidade:  {len(self.quantity_mismatches)}",
            f"  - Posições Fantasmas na B3:   {len(self.ghost_positions)}",
            f"  - Posições Faltantes na B3:   {len(self.missing_positions)}",
            "=" * 78,
        ]

        if self.breaks:
            lines.append("DETALHAMENTO DAS QUEBRAS:")
            for idx, b in enumerate(self.breaks, start=1):
                b3_val = str(b.quantidade_b3) if b.quantidade_b3 is not None else "AUSENTE"
                int_val = str(b.quantidade_interna) if b.quantidade_interna is not None else "AUSENTE"
                lines.append(
                    f"[{idx:02d}] Tipo: {b.break_type.value:<18} | Conta: {b.codigo_conta_custodia} | "
                    f"Ticker: {b.codigo_negociacao:<10} | B3: {b3_val:<8} | Interno: {int_val:<8} | "
                    f"Dif: {b.diferenca:+d}"
                )
                if b.detalhes:
                    lines.append(f"     Motivo: {b.detalhes}")
            lines.append("=" * 78)

        return "\n".join(lines)


class PositionReconciler:
    """Motor determinístico de batimento entre IMBARQ006 e livro de posições interno."""

    def __init__(self, allow_signed_quantities: bool = True) -> None:
        """Inicializa o reconciliador.

        Args:
            allow_signed_quantities: Se True, interpreta quantidade interna negativa (-Q)
                com sentido vendido ('V') como compatível com a representação B3 (Q, 'V').
        """
        self.allow_signed_quantities = allow_signed_quantities

    def _normalize_internal_positions(
        self, internal_positions: Iterable[Union[InternalPosition, Dict[str, Any]]]
    ) -> List[InternalPosition]:
        """Garante que todas as posições internas estejam tipadas como InternalPosition."""
        normalized: List[InternalPosition] = []
        for item in internal_positions:
            if isinstance(item, InternalPosition):
                normalized.append(item)
            elif isinstance(item, dict):
                normalized.append(InternalPosition.from_dict(item))
            else:
                raise TypeError(
                    f"Posição interna deve ser InternalPosition ou dict, recebido: {type(item)}"
                )
        return normalized

    def _resolve_b3_positions(
        self, b3_source: Union[Imbarq006File, Iterable[PositionDetailRecord], str, Path]
    ) -> Tuple[List[PositionDetailRecord], Optional[date]]:
        """Extrai os registros de detalhe da B3 e a data de movimento correspondente."""
        if isinstance(b3_source, Imbarq006File):
            return list(b3_source.detalhes), b3_source.header.data_movimento

        if isinstance(b3_source, (str, Path)):
            parsed = parse_imbarq006(b3_source)
            return list(parsed.detalhes), parsed.header.data_movimento

        # Iterable de PositionDetailRecord
        records = list(b3_source)
        return records, None

    def reconcile(
        self,
        b3_source: Union[Imbarq006File, Iterable[PositionDetailRecord], str, Path],
        internal_source: Iterable[Union[InternalPosition, Dict[str, Any]]],
        data_movimento: Optional[date] = None,
    ) -> ReconciliationReport:
        """Executa a conciliação completa entre a B3 e o livro interno.

        Args:
            b3_source: Arquivo IMBARQ006 parseado, caminho do arquivo ou lista de detalhes.
            internal_source: Coleção de posições internas (InternalPosition ou dicts).
            data_movimento: Data-base opcional da conciliação (extraída do header se omitida).

        Returns:
            ReconciliationReport estruturado e determinístico com matches e quebras.
        """
        b3_records, b3_date = self._resolve_b3_positions(b3_source)
        effective_date = data_movimento or b3_date

        internal_records = self._normalize_internal_positions(internal_source)

        # Agrupamento determinístico por chave de custódia (codigo_conta_custodia, codigo_negociacao)
        b3_by_key: Dict[Tuple[int, str], List[PositionDetailRecord]] = {}
        for rec in b3_records:
            key = (rec.codigo_conta_custodia, rec.codigo_negociacao.strip().upper())
            b3_by_key.setdefault(key, []).append(rec)

        internal_by_key: Dict[Tuple[int, str], List[InternalPosition]] = {}
        for pos in internal_records:
            key = (pos.codigo_conta_custodia, pos.codigo_negociacao.strip().upper())
            internal_by_key.setdefault(key, []).append(pos)

        all_keys = sorted(set(b3_by_key.keys()) | set(internal_by_key.keys()))

        matches: List[MatchedPosition] = []
        breaks: List[ReconciliationBreak] = []

        for conta, ticker in all_keys:
            key = (conta, ticker)
            in_b3 = key in b3_by_key
            in_internal = key in internal_by_key

            # Caso 1: Posição Fantasma na B3 (presente na câmara, ausente no livro interno)
            if in_b3 and not in_internal:
                b3_list = b3_by_key[key]
                total_b3_qty = sum(r.quantidade_liquida for r in b3_list)
                first_b3 = b3_list[0]
                breaks.append(
                    ReconciliationBreak(
                        break_type=BreakType.GHOST_IN_B3,
                        codigo_conta_custodia=conta,
                        codigo_negociacao=ticker,
                        quantidade_b3=total_b3_qty,
                        quantidade_interna=None,
                        diferenca=total_b3_qty,
                        b3_position=first_b3,
                        internal_position=None,
                        detalhes=f"Posição de {total_b3_qty} ativa na B3, porém não encontrada no livro interno da instituição.",
                    )
                )
                continue

            # Caso 2: Posição Faltante na B3 (presente no livro interno, ausente na câmara)
            if in_internal and not in_b3:
                int_list = internal_by_key[key]
                total_int_qty = sum(p.quantidade_liquida for p in int_list)
                first_int = int_list[0]
                breaks.append(
                    ReconciliationBreak(
                        break_type=BreakType.MISSING_IN_B3,
                        codigo_conta_custodia=conta,
                        codigo_negociacao=ticker,
                        quantidade_b3=None,
                        quantidade_interna=total_int_qty,
                        diferenca=-total_int_qty,
                        b3_position=None,
                        internal_position=first_int,
                        detalhes=f"Posição de {total_int_qty} registrada no livro interno, mas não reportada pelo IMBARQ006 da B3.",
                    )
                )
                continue

            # Caso 3: Posição presente em ambos — confrontar quantidade e sentido
            b3_list = b3_by_key[key]
            int_list = internal_by_key[key]

            first_b3 = b3_list[0]
            first_int = int_list[0]

            total_b3_qty = sum(r.quantidade_liquida for r in b3_list)
            raw_int_qty = sum(p.quantidade_liquida for p in int_list)

            # Normalização de quantidade líquida e sentido do livro interno
            int_sentido = first_int.sentido_posicao
            effective_int_qty = raw_int_qty

            if self.allow_signed_quantities and raw_int_qty < 0:
                effective_int_qty = abs(raw_int_qty)
                if not int_sentido:
                    int_sentido = "V"

            # Verificação de divergência de sentido (ex: B3 comprada 'C', interno vendido 'V')
            b3_sentido = first_b3.sentido_posicao
            sentido_divergente = False
            if int_sentido and b3_sentido and int_sentido != b3_sentido:
                sentido_divergente = True

            if sentido_divergente:
                diferenca = total_b3_qty - raw_int_qty
                breaks.append(
                    ReconciliationBreak(
                        break_type=BreakType.QUANTITY_MISMATCH,
                        codigo_conta_custodia=conta,
                        codigo_negociacao=ticker,
                        quantidade_b3=total_b3_qty,
                        quantidade_interna=raw_int_qty,
                        diferenca=diferenca,
                        b3_position=first_b3,
                        internal_position=first_int,
                        detalhes=(
                            f"Divergência de sentido de posição: B3 indica {b3_sentido} "
                            f"(qtd {total_b3_qty}), enquanto livro interno indica {int_sentido} "
                            f"(qtd {raw_int_qty})."
                        ),
                    )
                )
            elif total_b3_qty == effective_int_qty:
                # Match perfeito
                matches.append(
                    MatchedPosition(
                        codigo_conta_custodia=conta,
                        codigo_negociacao=ticker,
                        quantidade_liquida=total_b3_qty,
                        b3_position=first_b3,
                        internal_position=first_int,
                    )
                )
            else:
                # Quebra de quantidade
                diferenca = total_b3_qty - effective_int_qty
                breaks.append(
                    ReconciliationBreak(
                        break_type=BreakType.QUANTITY_MISMATCH,
                        codigo_conta_custodia=conta,
                        codigo_negociacao=ticker,
                        quantidade_b3=total_b3_qty,
                        quantidade_interna=raw_int_qty,
                        diferenca=diferenca,
                        b3_position=first_b3,
                        internal_position=first_int,
                        detalhes=(
                            f"Quebra quantitativa: B3 reporta {total_b3_qty}, "
                            f"livro interno possui {raw_int_qty} (diferença de {diferenca:+d})."
                        ),
                    )
                )

        return ReconciliationReport(
            data_movimento=effective_date,
            total_posicoes_b3=len(b3_records),
            total_posicoes_internas=len(internal_records),
            matches=matches,
            breaks=breaks,
        )


def reconcile_positions(
    b3_source: Union[Imbarq006File, Iterable[PositionDetailRecord], str, Path],
    internal_source: Iterable[Union[InternalPosition, Dict[str, Any]]],
    data_movimento: Optional[date] = None,
    allow_signed_quantities: bool = True,
) -> ReconciliationReport:
    """Função utilitária de conveniência para executar a conciliação de posições.

    Exemplo:
        >>> report = reconcile_positions(
        ...     b3_source="fixtures/imbarq006-valid.txt",
        ...     internal_source=[
        ...         InternalPosition(123456, "PETR4", 1000),
        ...         InternalPosition(123456, "PETRL350", 5000),
        ...         InternalPosition(789012, "WDOZ26", 10),
        ...     ]
        ... )
        >>> report.is_clean
        True
    """
    reconciler = PositionReconciler(allow_signed_quantities=allow_signed_quantities)
    return reconciler.reconcile(
        b3_source=b3_source,
        internal_source=internal_source,
        data_movimento=data_movimento,
    )
