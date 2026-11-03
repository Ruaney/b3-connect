"""Pipeline Unificado de Fechamento Diário de Pós-Negociação B3.

Este módulo orquestra a carga dos relatórios IMBARQ da grade noturna
(IMBARQ004, IMBARQ006, IMBARQ011, IMBARQ012, IMBARQ014), executa a conciliação
de custódia contra o livro interno da instituição e gera o sumário executivo
diário (DailyClosingSummary) com exportação estruturada em dict, JSON e Markdown.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
import io
import json
from pathlib import Path
import re
from typing import Any, Dict, IO, Iterable, List, Optional, Sequence, Tuple, Union

from src.imbarq004 import Imbarq004File, parse_imbarq004
from src.imbarq006 import Imbarq006File, PositionDetailRecord, parse_imbarq006
from src.imbarq011 import Imbarq011File, LoanContractRecord, parse_imbarq011
from src.imbarq012 import Imbarq012File, DepositaryPositionRecord, parse_imbarq012
from src.imbarq014 import CollateralMarginDetailRecord, Imbarq014File, parse_imbarq014
from src.reconciliation import (
    BreakType,
    InternalPosition,
    PositionReconciler,
    ReconciliationBreak,
    ReconciliationReport,
    reconcile_positions,
)


# ---------------------------------------------------------------------------
# Exceções Customizadas do Pipeline
# ---------------------------------------------------------------------------


class PipelineError(Exception):
    """Exceção base para erros ocorridos durante o pipeline de fechamento diário."""

    pass


class PipelineConfigurationError(PipelineError):
    """Lançada quando a configuração ou os arquivos fornecidos ao pipeline são insuficientes ou inválidos."""

    pass


class PipelineDateMismatchError(PipelineError):
    """Lançada quando há divergência de data de movimento entre os arquivos IMBARQ da grade noturna."""

    pass


class PipelineParticipantMismatchError(PipelineError):
    """Lançada quando há divergência de código de participante entre os arquivos IMBARQ."""

    pass


# ---------------------------------------------------------------------------
# Funções Auxiliares de Formatação
# ---------------------------------------------------------------------------


def _fmt_currency(val: Union[Decimal, float, int]) -> str:
    """Formata valor monetário no padrão brasileiro (R$ X.XXX,XX)."""
    dec_val = Decimal(str(val))
    formatted = f"{dec_val:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return f"R$ {formatted}"


def _fmt_int(val: int) -> str:
    """Formata número inteiro com separador de milhar brasileiro (X.XXX)."""
    return f"{val:,}".replace(",", ".")


# ---------------------------------------------------------------------------
# Sumário Executivo de Fechamento Diário (DailyClosingSummary)
# ---------------------------------------------------------------------------


@dataclass
class DailyClosingSummary:
    """Sumário executivo consolidado do fechamento diário da instituição pós-negociação B3."""

    # Metadados do fechamento
    data_movimento: Optional[date]
    codigo_participante: Optional[int]
    nome_participante: Optional[str]
    timestamp_processamento: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    # 1. Posições na Câmara (IMBARQ006) & Conciliação de Custódia
    total_posicoes_camara: int = 0
    total_quantidade_liquida_camara: int = 0
    total_valor_camara: Decimal = Decimal("0.00")
    status_conciliacao: str = "NAO_CONCILIADO"  # "CLEAN", "QUEBRAS", "NAO_CONCILIADO"
    is_clean: bool = False
    total_posicoes_internas: int = 0
    total_matches: int = 0
    total_quebras: int = 0
    reconciliation_report: Optional[ReconciliationReport] = None

    # 2. Saldo Financeiro de Liquidação (IMBARQ004)
    total_lancamentos: int = 0
    total_debitos: Decimal = Decimal("0.00")
    total_creditos: Decimal = Decimal("0.00")
    saldo_liquido: Decimal = Decimal("0.00")
    sinal_saldo_liquido: str = "C"

    # 3. Empréstimo de Ativos BTC (IMBARQ011)
    total_contratos_btc: int = 0
    quantidade_total_btc: int = 0
    volume_total_btc: Decimal = Decimal("0.00")

    # 4. Custódia na Depositária CSD (IMBARQ012)
    total_posicoes_csd: int = 0
    quantidade_total_csd: int = 0
    quantidade_livre_csd: int = 0
    quantidade_bloqueada_csd: int = 0
    valor_carteira_csd: Decimal = Decimal("0.00")

    # 5. Colateral e Chamadas de Margem (IMBARQ014)
    total_colaterais: int = 0
    margem_requerida_total: Decimal = Decimal("0.00")
    valor_avaliacao_total: Decimal = Decimal("0.00")
    margem_livre_total: Decimal = Decimal("0.00")
    defasagem_total: Decimal = Decimal("0.00")
    total_chamadas_margem: int = 0
    tem_chamada_margem: bool = False

    # Instâncias dos arquivos brutos processados
    imbarq004: Optional[Imbarq004File] = None
    imbarq006: Optional[Imbarq006File] = None
    imbarq011: Optional[Imbarq011File] = None
    imbarq012: Optional[Imbarq012File] = None
    imbarq014: Optional[Imbarq014File] = None

    # Propriedades de conveniência
    @property
    def is_conciliado_clean(self) -> bool:
        """Indica se a conciliação foi 100% batida sem quebras."""
        return self.is_clean

    @property
    def total_posicoes(self) -> int:
        """Alias para o total de posições na câmara (IMBARQ006)."""
        return self.total_posicoes_camara

    @property
    def contratos_btc(self) -> int:
        """Alias para total de contratos de empréstimo BTC."""
        return self.total_contratos_btc

    @property
    def volume_btc(self) -> Decimal:
        """Alias para volume financeiro de empréstimo BTC."""
        return self.volume_total_btc

    @property
    def quantidade_livre(self) -> int:
        """Alias para quantidade livre em custódia CSD."""
        return self.quantidade_livre_csd

    @property
    def quantidade_bloqueada(self) -> int:
        """Alias para quantidade bloqueada em custódia CSD."""
        return self.quantidade_bloqueada_csd

    @property
    def margem_livre(self) -> Decimal:
        """Alias para margem livre total de garantias."""
        return self.margem_livre_total

    @property
    def defasagem(self) -> Decimal:
        """Alias para defasagem / chamada de margem total."""
        return self.defasagem_total

    def to_dict(self) -> Dict[str, Any]:
        """Converte o sumário diário em um dicionário estruturado e serializável."""
        # Lista detalhada de quebras de conciliação, se houver
        quebras_list: List[Dict[str, Any]] = []
        if self.reconciliation_report and self.reconciliation_report.breaks:
            for b in self.reconciliation_report.breaks:
                quebras_list.append(
                    {
                        "break_type": b.break_type.value,
                        "codigo_conta_custodia": b.codigo_conta_custodia,
                        "codigo_negociacao": b.codigo_negociacao,
                        "quantidade_b3": b.quantidade_b3,
                        "quantidade_interna": b.quantidade_interna,
                        "diferenca": b.diferenca,
                        "detalhes": b.detalhes,
                    }
                )

        # Status geral da instituição
        if not self.is_clean:
            status_geral = "QUEBRAS_CONCILIACAO"
        elif self.tem_chamada_margem:
            status_geral = "CHAMADA_MARGEM_PENDENTE"
        else:
            status_geral = "REGULAR_CLEAN"

        dt_iso = self.data_movimento.isoformat() if self.data_movimento else None
        ts_iso = self.timestamp_processamento.isoformat()

        return {
            "data_movimento": dt_iso,
            "codigo_participante": self.codigo_participante,
            "nome_participante": self.nome_participante,
            "timestamp_processamento": ts_iso,
            "status_geral": status_geral,
            # Top-level direct shortcuts para acesso direto
            "total_posicoes_camara": self.total_posicoes_camara,
            "status_conciliacao": self.status_conciliacao,
            "is_clean": self.is_clean,
            "total_matches": self.total_matches,
            "total_quebras": self.total_quebras,
            "total_debitos": float(self.total_debitos),
            "total_creditos": float(self.total_creditos),
            "saldo_liquido": float(self.saldo_liquido),
            "sinal_saldo_liquido": self.sinal_saldo_liquido,
            "total_contratos_btc": self.total_contratos_btc,
            "volume_total_btc": float(self.volume_total_btc),
            "quantidade_total_btc": self.quantidade_total_btc,
            "total_posicoes_csd": self.total_posicoes_csd,
            "quantidade_total_csd": self.quantidade_total_csd,
            "quantidade_livre_csd": self.quantidade_livre_csd,
            "quantidade_bloqueada_csd": self.quantidade_bloqueada_csd,
            "valor_carteira_csd": float(self.valor_carteira_csd),
            "total_colaterais": self.total_colaterais,
            "margem_requerida_total": float(self.margem_requerida_total),
            "valor_avaliacao_total": float(self.valor_avaliacao_total),
            "margem_livre_total": float(self.margem_livre_total),
            "defasagem_total": float(self.defasagem_total),
            "total_chamadas_margem": self.total_chamadas_margem,
            "tem_chamada_margem": self.tem_chamada_margem,
            # Seções modulares estruturadas
            "posicoes_camara": {
                "layout": "IMBARQ006",
                "total_posicoes": self.total_posicoes_camara,
                "total_quantidade_liquida": self.total_quantidade_liquida_camara,
                "total_valor_financeiro": float(self.total_valor_camara),
                "status_conciliacao": self.status_conciliacao,
                "is_clean": self.is_clean,
                "total_posicoes_internas": self.total_posicoes_internas,
                "total_matches": self.total_matches,
                "total_quebras": self.total_quebras,
                "quebras": quebras_list,
            },
            "liquidacao_financeira": {
                "layout": "IMBARQ004",
                "total_lancamentos": self.total_lancamentos,
                "total_debitos": float(self.total_debitos),
                "total_creditos": float(self.total_creditos),
                "saldo_liquido": float(self.saldo_liquido),
                "sinal_saldo": self.sinal_saldo_liquido,
            },
            "emprestimo_btc": {
                "layout": "IMBARQ011",
                "total_contratos": self.total_contratos_btc,
                "quantidade_total": self.quantidade_total_btc,
                "volume_total": float(self.volume_total_btc),
            },
            "custodia_depositaria_csd": {
                "layout": "IMBARQ012",
                "total_posicoes": self.total_posicoes_csd,
                "quantidade_total": self.quantidade_total_csd,
                "quantidade_livre": self.quantidade_livre_csd,
                "quantidade_bloqueada": self.quantidade_bloqueada_csd,
                "valor_carteira": float(self.valor_carteira_csd),
            },
            "colateral_margem": {
                "layout": "IMBARQ014",
                "total_colaterais": self.total_colaterais,
                "margem_requerida_total": float(self.margem_requerida_total),
                "valor_avaliacao_total": float(self.valor_avaliacao_total),
                "margem_livre_total": float(self.margem_livre_total),
                "defasagem_total": float(self.defasagem_total),
                "total_chamadas_margem": self.total_chamadas_margem,
                "tem_chamada_margem": self.tem_chamada_margem,
            },
        }

    def to_json(
        self, output_path: Optional[Union[str, Path]] = None, indent: int = 2
    ) -> str:
        """Serializa o sumário diário em formato JSON.

        Args:
            output_path: Se informado, salva o conteúdo JSON no caminho indicado.
            indent: Quantidade de espaços para indentação do JSON.

        Returns:
            String JSON formatada.
        """
        payload = json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)
        if output_path is not None:
            path_obj = Path(output_path)
            path_obj.parent.mkdir(parents=True, exist_ok=True)
            path_obj.write_text(payload, encoding="utf-8")
        return payload

    def to_markdown_report(
        self, output_path: Optional[Union[str, Path]] = None
    ) -> str:
        """Gera relatório executivo formatado em Markdown para auditoria e governança.

        Args:
            output_path: Se informado, salva o relatório Markdown no arquivo especificado.

        Returns:
            String contendo o relatório completo em formato Markdown.
        """
        dt_str = self.data_movimento.isoformat() if self.data_movimento else "N/A"
        part_code_str = (
            f"{self.codigo_participante:08d}"
            if self.codigo_participante is not None
            else "N/A"
        )
        part_nome_str = self.nome_participante or "NÃO IDENTIFICADO"
        ts_str = self.timestamp_processamento.strftime("%Y-%m-%d %H:%M:%S UTC")

        # Status geral badge
        if self.is_clean and not self.tem_chamada_margem:
            status_badge = "🟢 CONCILIADO & REGULAR"
        elif not self.is_clean and self.tem_chamada_margem:
            status_badge = "🔴 QUEBRAS DE CUSTÓDIA & DEFASAGEM DE MARGEM"
        elif not self.is_clean:
            status_badge = "🔴 QUEBRAS DE CUSTÓDIA IDENTIFICADAS"
        else:
            status_badge = "🟡 ALERTA: DEFASAGEM / CHAMADA DE MARGEM ATIVA"

        # Cálculo de proporção de custódia livre vs bloqueada CSD
        if self.quantidade_total_csd > 0:
            pct_livre = (self.quantidade_livre_csd / self.quantidade_total_csd) * 100
            pct_bloq = (self.quantidade_bloqueada_csd / self.quantidade_total_csd) * 100
            str_csd_dist = (
                f"- **Quantidade Disponível (Livre):** {_fmt_int(self.quantidade_livre_csd)} ({pct_livre:.2f}%)\n"
                f"- **Quantidade Bloqueada:** {_fmt_int(self.quantidade_bloqueada_csd)} ({pct_bloq:.2f}%)"
            )
        else:
            str_csd_dist = (
                f"- **Quantidade Disponível (Livre):** {_fmt_int(self.quantidade_livre_csd)}\n"
                f"- **Quantidade Bloqueada:** {_fmt_int(self.quantidade_bloqueada_csd)}"
            )

        # Sinalização contábil de liquidação financeira
        sinal_desc = "CREDOR" if self.saldo_liquido >= 0 else "DEVEDOR"

        lines = [
            "# Fechamento Diário Pós-Negociação B3 — Sumário Executivo",
            "",
            f"- **Data de Movimento:** `{dt_str}`",
            f"- **Código do Participante:** `{part_code_str}`",
            f"- **Instituição:** **{part_nome_str}**",
            f"- **Data/Hora de Processamento:** `{ts_str}`",
            f"- **Status Geral:** {status_badge}",
            "",
            "---",
            "",
            "## 1. Posições na Câmara & Conciliação de Custódia (IMBARQ006)",
            f"- **Total de Posições na B3 (Câmara):** {_fmt_int(self.total_posicoes_camara)}",
            f"- **Total de Posições Livro Interno:** {_fmt_int(self.total_posicoes_internas)}",
            f"- **Status da Conciliação:** **{self.status_conciliacao}**",
            f"- **Matches Perfeitos:** {_fmt_int(self.total_matches)}",
            f"- **Quebras Detectadas:** {_fmt_int(self.total_quebras)}",
            f"- **Quantidade Líquida Total B3:** {_fmt_int(self.total_quantidade_liquida_camara)}",
            f"- **Valor Financeiro Total B3:** {_fmt_currency(self.total_valor_camara)}",
            "",
            "## 2. Liquidação Financeira Multilateral (IMBARQ004)",
            f"- **Total de Lançamentos:** {_fmt_int(self.total_lancamentos)}",
            f"- **Total Débitos:** {_fmt_currency(self.total_debitos)}",
            f"- **Total Créditos:** {_fmt_currency(self.total_creditos)}",
            f"- **Saldo Líquido:** {_fmt_currency(self.saldo_liquido)} ({sinal_desc})",
            "",
            "## 3. Empréstimo de Ativos / BTC (IMBARQ011)",
            f"- **Total de Contratos:** {_fmt_int(self.total_contratos_btc)}",
            f"- **Quantidade Total de Ativos Alugados:** {_fmt_int(self.quantidade_total_btc)}",
            f"- **Volume Financeiro Total:** {_fmt_currency(self.volume_total_btc)}",
            "",
            "## 4. Custódia na Depositária Central CSD (IMBARQ012)",
            f"- **Total de Posições Custodiadas:** {_fmt_int(self.total_posicoes_csd)}",
            f"- **Quantidade Total de Ativos:** {_fmt_int(self.quantidade_total_csd)}",
            str_csd_dist,
            f"- **Valor Total em Custódia:** {_fmt_currency(self.valor_carteira_csd)}",
            "",
            "## 5. Garantias e Chamadas de Margem (IMBARQ014)",
            f"- **Total de Colaterais Alocados:** {_fmt_int(self.total_colaterais)}",
            f"- **Valor Total de Avaliação:** {_fmt_currency(self.valor_avaliacao_total)}",
            f"- **Margem Requerida pela B3:** {_fmt_currency(self.margem_requerida_total)}",
            f"- **Margem Livre Total:** {_fmt_currency(self.margem_livre_total)}",
            f"- **Defasagem / Chamada de Margem:** {_fmt_currency(self.defasagem_total)}",
            f"- **Total de Chamadas de Margem Ativas:** {_fmt_int(self.total_chamadas_margem)}",
        ]

        # Se houver quebras de conciliação, acrescenta tabela analítica
        if self.reconciliation_report and self.reconciliation_report.breaks:
            lines.extend(
                [
                    "",
                    "### ⚠️ Detalhamento das Quebras de Conciliação de Custódia",
                    "",
                    "| # | Tipo de Quebra | Conta | Ticker | B3 | Livro Interno | Diferença | Detalhes |",
                    "|---|---|---|---|---|---|---|---|",
                ]
            )
            for idx, b in enumerate(self.reconciliation_report.breaks, start=1):
                b3_v = str(b.quantidade_b3) if b.quantidade_b3 is not None else "-"
                int_v = (
                    str(b.quantidade_interna)
                    if b.quantidade_interna is not None
                    else "-"
                )
                dif_v = f"{b.diferenca:+d}"
                det_v = b.detalhes.replace("|", "/")
                lines.append(
                    f"| {idx:02d} | `{b.break_type.value}` | `{b.codigo_conta_custodia}` | "
                    f"**{b.codigo_negociacao}** | {b3_v} | {int_v} | {dif_v} | {det_v} |"
                )

        # Se houver chamadas de margem no IMBARQ014, lista os detalhes
        if self.imbarq014 and self.total_chamadas_margem > 0:
            lines.extend(
                [
                    "",
                    "### ⚠️ Detalhamento das Chamadas de Margem / Defasagens (IMBARQ014)",
                    "",
                    "| # | Conta | Tipo | Ativo | ISIN | Margem Requerida | Defasagem a Cobrir |",
                    "|---|---|---|---|---|---|---|",
                ]
            )
            for idx, c in enumerate(self.imbarq014.get_chamadas_margem(), start=1):
                lines.append(
                    f"| {idx:02d} | `{c.codigo_conta_custodia}` | `{c.tipo_colateral}` | "
                    f"**{c.identificador_ativo}** | `{c.codigo_isin}` | "
                    f"{_fmt_currency(c.margem_requerida)} | {_fmt_currency(c.valor_margem_livre_defasagem)} |"
                )

        lines.append("")
        content = "\n".join(lines)

        if output_path is not None:
            path_obj = Path(output_path)
            path_obj.parent.mkdir(parents=True, exist_ok=True)
            path_obj.write_text(content, encoding="utf-8")

        return content


# ---------------------------------------------------------------------------
# Orquestrador do Pipeline Unificado (PostTradePipeline)
# ---------------------------------------------------------------------------


class PostTradePipeline:
    """Orquestrador do Pipeline Unificado de Fechamento Diário de Pós-Negociação B3."""

    def __init__(
        self,
        imbarq004: Optional[
            Union[Imbarq004File, str, Path, IO[str], Iterable[str]]
        ] = None,
        imbarq006: Optional[
            Union[Imbarq006File, str, Path, IO[str], Iterable[str]]
        ] = None,
        imbarq011: Optional[
            Union[Imbarq011File, str, Path, IO[str], Iterable[str]]
        ] = None,
        imbarq012: Optional[
            Union[Imbarq012File, str, Path, IO[str], Iterable[str]]
        ] = None,
        imbarq014: Optional[
            Union[Imbarq014File, str, Path, IO[str], Iterable[str]]
        ] = None,
        internal_positions: Optional[
            Iterable[Union[InternalPosition, Dict[str, Any]]]
        ] = None,
        data_movimento: Optional[Union[date, str]] = None,
        strict_date_validation: bool = True,
        strict_participant_validation: bool = True,
    ) -> None:
        """Inicializa a instância do pipeline.

        Args:
            imbarq004: Relatório de liquidação financeira (arquivo, path ou objeto parseado).
            imbarq006: Relatório de posições da câmara B3.
            imbarq011: Relatório de contratos de empréstimo BTC.
            imbarq012: Relatório de custódia na depositária central CSD.
            imbarq014: Relatório de garantias e margens (CORE).
            internal_positions: Livro de custódia interno para reconciliação.
            data_movimento: Data-base de movimento esperada para o fechamento.
            strict_date_validation: Se True, exige que todos os arquivos tenham a mesma data_movimento.
            strict_participant_validation: Se True, exige que todos os arquivos tenham o mesmo codigo_participante.
        """
        self.strict_date_validation = strict_date_validation
        self.strict_participant_validation = strict_participant_validation

        self.data_movimento: Optional[date] = (
            date.fromisoformat(str(data_movimento))
            if data_movimento is not None and not isinstance(data_movimento, date)
            else data_movimento
        )

        self.file_004: Optional[Imbarq004File] = None
        self.file_006: Optional[Imbarq006File] = None
        self.file_011: Optional[Imbarq011File] = None
        self.file_012: Optional[Imbarq012File] = None
        self.file_014: Optional[Imbarq014File] = None
        self.internal_positions: Optional[List[InternalPosition]] = None

        if imbarq004 is not None:
            self.load_imbarq004(imbarq004)
        if imbarq006 is not None:
            self.load_imbarq006(imbarq006)
        if imbarq011 is not None:
            self.load_imbarq011(imbarq011)
        if imbarq012 is not None:
            self.load_imbarq012(imbarq012)
        if imbarq014 is not None:
            self.load_imbarq014(imbarq014)
        if internal_positions is not None:
            self.load_internal_positions(internal_positions)

        self.last_summary: Optional[DailyClosingSummary] = None

    # -----------------------------------------------------------------------
    # Métodos de Carregamento Modular de Relatórios
    # -----------------------------------------------------------------------

    def load_imbarq004(
        self, source: Union[Imbarq004File, str, Path, IO[str], Iterable[str]]
    ) -> Imbarq004File:
        """Carrega e valida o relatório financeiro de liquidação IMBARQ004."""
        if isinstance(source, Imbarq004File):
            self.file_004 = source
        else:
            self.file_004 = parse_imbarq004(source)
        return self.file_004

    def load_imbarq006(
        self, source: Union[Imbarq006File, str, Path, IO[str], Iterable[str]]
    ) -> Imbarq006File:
        """Carrega e valida o relatório de posições em aberto na câmara IMBARQ006."""
        if isinstance(source, Imbarq006File):
            self.file_006 = source
        else:
            self.file_006 = parse_imbarq006(source)
        return self.file_006

    def load_imbarq011(
        self, source: Union[Imbarq011File, str, Path, IO[str], Iterable[str]]
    ) -> Imbarq011File:
        """Carrega e valida o relatório de contratos de empréstimo BTC IMBARQ011."""
        if isinstance(source, Imbarq011File):
            self.file_011 = source
        else:
            self.file_011 = parse_imbarq011(source)
        return self.file_011

    def load_imbarq012(
        self,
        source: Union[Imbarq012File, str, Path, IO[str], Iterable[str]],
        strict_consistency: bool = True,
    ) -> Imbarq012File:
        """Carrega e valida o relatório de posições da depositária CSD IMBARQ012."""
        if isinstance(source, Imbarq012File):
            self.file_012 = source
        else:
            self.file_012 = parse_imbarq012(
                source, strict_consistency=strict_consistency
            )
        return self.file_012

    def load_imbarq014(
        self, source: Union[Imbarq014File, str, Path, IO[str], Iterable[str]]
    ) -> Imbarq014File:
        """Carrega e valida o relatório de garantias e margens IMBARQ014."""
        if isinstance(source, Imbarq014File):
            self.file_014 = source
        else:
            self.file_014 = parse_imbarq014(source)
        return self.file_014

    def load_internal_positions(
        self, positions: Iterable[Union[InternalPosition, Dict[str, Any]]]
    ) -> List[InternalPosition]:
        """Normaliza e carrega o livro interno de custódia da instituição."""
        normalized: List[InternalPosition] = []
        for pos in positions:
            if isinstance(pos, InternalPosition):
                normalized.append(pos)
            elif isinstance(pos, dict):
                normalized.append(InternalPosition.from_dict(pos))
            else:
                raise TypeError(
                    f"Posição interna deve ser InternalPosition ou dict, recebido {type(pos)}"
                )
        self.internal_positions = normalized
        return self.internal_positions

    # -----------------------------------------------------------------------
    # Descoberta e Carga Automática de Diretório
    # -----------------------------------------------------------------------

    @staticmethod
    def _find_report_in_dir(
        dir_path: Path, code: str, custom_pattern: Optional[str] = None
    ) -> Optional[Path]:
        """Localiza deterministicamente um arquivo IMBARQ dentro de um diretório."""
        if custom_pattern:
            matches = list(dir_path.glob(custom_pattern))
            if matches:
                return sorted(matches)[0]

        code_str = code.lower()
        candidates: List[Path] = []
        for item in dir_path.iterdir():
            if not item.is_file():
                continue
            name_lower = item.name.lower()
            if code_str in name_lower and ("imbarq" in name_lower or name_lower.startswith(code_str)):
                candidates.append(item)

        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]

        # Prioriza arquivos terminados em '-valid.txt' ou que contenham 'valid'
        valid_candidates = [c for c in candidates if "valid" in c.name.lower()]
        if valid_candidates:
            return sorted(valid_candidates)[0]

        # Exclui arquivos com padrões de erro conhecidos
        good_candidates = [
            c
            for c in candidates
            if not any(w in c.name.lower() for w in ("bad", "error", "invalid"))
        ]
        if good_candidates:
            return sorted(good_candidates)[0]

        return sorted(candidates)[0]

    def load_from_directory(
        self,
        directory: Union[str, Path],
        file_patterns: Optional[Dict[str, str]] = None,
        require_all: bool = True,
    ) -> PostTradePipeline:
        """Varre um diretório e carrega os 5 arquivos IMBARQ da grade noturna.

        Args:
            directory: Caminho da pasta contendo os relatórios B3.
            file_patterns: Dicionário customizado com padrões para cada código ('004', '006', etc.).
            require_all: Se True, exige a localização de todos os 5 relatórios.

        Returns:
            A própria instância do pipeline (fluent interface).
        """
        dir_path = Path(directory)
        if not dir_path.is_dir():
            raise PipelineConfigurationError(
                f"Diretório informado não existe ou não é uma pasta: {dir_path}"
            )

        patterns = file_patterns or {}
        loaders: List[Tuple[str, Any]] = [
            ("004", self.load_imbarq004),
            ("006", self.load_imbarq006),
            ("011", self.load_imbarq011),
            ("012", self.load_imbarq012),
            ("014", self.load_imbarq014),
        ]

        missing: List[str] = []
        for code, loader in loaders:
            custom_pat = (
                patterns.get(code)
                or patterns.get(f"imbarq{code}")
                or patterns.get(f"IMBARQ{code}")
            )
            found_file = self._find_report_in_dir(
                dir_path, code=code, custom_pattern=custom_pat
            )
            if found_file is not None:
                loader(found_file)
            else:
                missing.append(f"IMBARQ{code}")

        if require_all and missing:
            raise PipelineConfigurationError(
                f"Arquivos da grade noturna não localizados no diretório {dir_path}: {', '.join(missing)}"
            )

        return self

    @classmethod
    def from_directory(
        cls,
        directory: Union[str, Path],
        internal_positions: Optional[
            Iterable[Union[InternalPosition, Dict[str, Any]]]
        ] = None,
        data_movimento: Optional[Union[date, str]] = None,
        file_patterns: Optional[Dict[str, str]] = None,
        strict_date_validation: bool = True,
        strict_participant_validation: bool = True,
    ) -> PostTradePipeline:
        """Cria e carrega uma instância do pipeline a partir de uma pasta de relatórios."""
        instance = cls(
            internal_positions=internal_positions,
            data_movimento=data_movimento,
            strict_date_validation=strict_date_validation,
            strict_participant_validation=strict_participant_validation,
        )
        instance.load_from_directory(directory, file_patterns=file_patterns)
        return instance

    # -----------------------------------------------------------------------
    # Validações de Consistência Multilateral (Data & Participante)
    # -----------------------------------------------------------------------

    def _validate_consistency(
        self, files: Sequence[Any], expected_date: Optional[date]
    ) -> Tuple[Optional[date], Optional[int], Optional[str]]:
        """Valida que todos os arquivos carregados pertençam à mesma data e participante."""
        if not files:
            return expected_date, None, None

        # Validação de Data de Movimento
        dates = {f.header.data_movimento for f in files if getattr(f, "header", None)}
        if expected_date is not None:
            if self.strict_date_validation and dates and dates != {expected_date}:
                raise PipelineDateMismatchError(
                    f"Divergência entre data esperada ({expected_date}) e datas nos relatórios: {dates}"
                )
            effective_date = expected_date
        else:
            if self.strict_date_validation and len(dates) > 1:
                raise PipelineDateMismatchError(
                    f"Divergência de data de movimento entre os arquivos IMBARQ da grade noturna: {dates}"
                )
            effective_date = next(iter(dates)) if dates else None

        # Validação de Participante
        participants = {
            f.header.codigo_participante for f in files if getattr(f, "header", None)
        }
        if self.strict_participant_validation and len(participants) > 1:
            raise PipelineParticipantMismatchError(
                f"Divergência de código de participante entre os arquivos carregados: {participants}"
            )
        part_code = next(iter(participants)) if participants else None

        # Nome do participante (do primeiro arquivo com header preenchido)
        part_name = next(
            (
                f.header.nome_participante
                for f in files
                if getattr(f, "header", None) and f.header.nome_participante
            ),
            None,
        )

        return effective_date, part_code, part_name

    # -----------------------------------------------------------------------
    # Execução Principal do Fechamento Diário
    # -----------------------------------------------------------------------

    def execute(
        self,
        imbarq004: Optional[
            Union[Imbarq004File, str, Path, IO[str], Iterable[str]]
        ] = None,
        imbarq006: Optional[
            Union[Imbarq006File, str, Path, IO[str], Iterable[str]]
        ] = None,
        imbarq011: Optional[
            Union[Imbarq011File, str, Path, IO[str], Iterable[str]]
        ] = None,
        imbarq012: Optional[
            Union[Imbarq012File, str, Path, IO[str], Iterable[str]]
        ] = None,
        imbarq014: Optional[
            Union[Imbarq014File, str, Path, IO[str], Iterable[str]]
        ] = None,
        internal_positions: Optional[
            Iterable[Union[InternalPosition, Dict[str, Any]]]
        ] = None,
        data_movimento: Optional[Union[date, str]] = None,
        require_all_files: bool = True,
    ) -> DailyClosingSummary:
        """Executa a consolidação do fechamento diário e a conciliação de custódia.

        Args:
            imbarq004: Opcional, substitui ou define o arquivo IMBARQ004.
            imbarq006: Opcional, substitui ou define o arquivo IMBARQ006.
            imbarq011: Opcional, substitui ou define o arquivo IMBARQ011.
            imbarq012: Opcional, substitui ou define o arquivo IMBARQ012.
            imbarq014: Opcional, substitui ou define o arquivo IMBARQ014.
            internal_positions: Livro interno de custódia para conciliação contra IMBARQ006.
            data_movimento: Data-base de movimento para validação.
            require_all_files: Se True, exige que todos os 5 arquivos estejam carregados.

        Returns:
            Instância consolidada de DailyClosingSummary.
        """
        if imbarq004 is not None:
            self.load_imbarq004(imbarq004)
        if imbarq006 is not None:
            self.load_imbarq006(imbarq006)
        if imbarq011 is not None:
            self.load_imbarq011(imbarq011)
        if imbarq012 is not None:
            self.load_imbarq012(imbarq012)
        if imbarq014 is not None:
            self.load_imbarq014(imbarq014)
        if internal_positions is not None:
            self.load_internal_positions(internal_positions)

        # Checagem de obrigatoriedade dos relatórios
        if require_all_files:
            missing_reports = []
            if self.file_004 is None:
                missing_reports.append("IMBARQ004")
            if self.file_006 is None:
                missing_reports.append("IMBARQ006")
            if self.file_011 is None:
                missing_reports.append("IMBARQ011")
            if self.file_012 is None:
                missing_reports.append("IMBARQ012")
            if self.file_014 is None:
                missing_reports.append("IMBARQ014")

            if missing_reports:
                raise PipelineConfigurationError(
                    "Relatórios IMBARQ obrigatórios ausentes para o fechamento diário completo: "
                    + ", ".join(missing_reports)
                )

        target_date = (
            date.fromisoformat(str(data_movimento))
            if data_movimento is not None and not isinstance(data_movimento, date)
            else (data_movimento or self.data_movimento)
        )

        loaded_files = [
            f
            for f in (
                self.file_004,
                self.file_006,
                self.file_011,
                self.file_012,
                self.file_014,
            )
            if f is not None
        ]

        effective_date, part_code, part_name = self._validate_consistency(
            loaded_files, expected_date=target_date
        )

        # -------------------------------------------------------------------
        # 1. Posições na Câmara & Conciliação de Custódia (IMBARQ006)
        # -------------------------------------------------------------------
        f006 = self.file_006
        tot_pos_camara = f006.total_posicoes if f006 else 0
        tot_qtd_camara = f006.trailer.total_quantidade_liquida if f006 else 0
        tot_val_camara = (
            f006.trailer.total_valor_financeiro if f006 else Decimal("0.00")
        )

        recon_report: Optional[ReconciliationReport] = None
        if f006 is not None and self.internal_positions is not None:
            recon_report = reconcile_positions(
                b3_source=f006,
                internal_source=self.internal_positions,
                data_movimento=effective_date,
            )
            status_conciliacao = "CLEAN" if recon_report.is_clean else "QUEBRAS"
            is_clean = recon_report.is_clean
            tot_matches = recon_report.total_matches
            tot_quebras = recon_report.total_breaks
            tot_internas = recon_report.total_posicoes_internas
        else:
            status_conciliacao = "NAO_CONCILIADO"
            is_clean = False
            tot_matches = 0
            tot_quebras = 0
            tot_internas = len(self.internal_positions) if self.internal_positions else 0

        # -------------------------------------------------------------------
        # 2. Saldo Financeiro de Liquidação (IMBARQ004)
        # -------------------------------------------------------------------
        f004 = self.file_004
        tot_lanc = f004.total_lancamentos if f004 else 0
        tot_deb = f004.total_debitos if f004 else Decimal("0.00")
        tot_cred = f004.total_creditos if f004 else Decimal("0.00")
        saldo_liq = f004.saldo_liquido if f004 else Decimal("0.00")
        sinal_saldo = (
            f004.trailer.sinal_saldo_liquido
            if f004
            else ("C" if saldo_liq >= 0 else "D")
        )

        # -------------------------------------------------------------------
        # 3. Empréstimo BTC (IMBARQ011)
        # -------------------------------------------------------------------
        f011 = self.file_011
        tot_contratos_btc = f011.total_contratos if f011 else 0
        tot_qtd_btc = f011.trailer.total_quantidade_alugada if f011 else 0
        vol_total_btc = (
            f011.trailer.total_valor_contratos if f011 else Decimal("0.00")
        )

        # -------------------------------------------------------------------
        # 4. Custódia na Depositária CSD (IMBARQ012)
        # -------------------------------------------------------------------
        f012 = self.file_012
        tot_pos_csd = f012.total_posicoes if f012 else 0
        tot_qtd_csd = f012.trailer.total_quantidade_total if f012 else 0
        qtd_livre_csd = f012.trailer.total_quantidade_disponivel if f012 else 0
        qtd_bloq_csd = f012.trailer.total_quantidade_bloqueada if f012 else 0
        val_carteira_csd = f012.valor_total_carteira if f012 else Decimal("0.00")

        # -------------------------------------------------------------------
        # 5. Garantias e Chamadas de Margem (IMBARQ014)
        # -------------------------------------------------------------------
        f014 = self.file_014
        tot_colaterais = f014.total_colaterais if f014 else 0
        tot_mrg_req = (
            f014.trailer.total_margem_requerida if f014 else Decimal("0.00")
        )
        tot_val_aval = (
            f014.trailer.total_valor_avaliacao if f014 else Decimal("0.00")
        )
        mrg_livre_total = (
            sum(
                (d.valor_margem_livre_defasagem for d in f014.get_margens_livres()),
                Decimal("0.00"),
            )
            if f014
            else Decimal("0.00")
        )
        defasagem_total = (
            sum(
                (d.valor_margem_livre_defasagem for d in f014.get_chamadas_margem()),
                Decimal("0.00"),
            )
            if f014
            else Decimal("0.00")
        )
        tot_chamadas = len(f014.get_chamadas_margem()) if f014 else 0
        tem_chamada = tot_chamadas > 0

        # Criação do objeto estruturado DailyClosingSummary
        summary = DailyClosingSummary(
            data_movimento=effective_date,
            codigo_participante=part_code,
            nome_participante=part_name,
            total_posicoes_camara=tot_pos_camara,
            total_quantidade_liquida_camara=tot_qtd_camara,
            total_valor_camara=tot_val_camara,
            status_conciliacao=status_conciliacao,
            is_clean=is_clean,
            total_posicoes_internas=tot_internas,
            total_matches=tot_matches,
            total_quebras=tot_quebras,
            reconciliation_report=recon_report,
            total_lancamentos=tot_lanc,
            total_debitos=tot_deb,
            total_creditos=tot_cred,
            saldo_liquido=saldo_liq,
            sinal_saldo_liquido=sinal_saldo,
            total_contratos_btc=tot_contratos_btc,
            quantidade_total_btc=tot_qtd_btc,
            volume_total_btc=vol_total_btc,
            total_posicoes_csd=tot_pos_csd,
            quantidade_total_csd=tot_qtd_csd,
            quantidade_livre_csd=qtd_livre_csd,
            quantidade_bloqueada_csd=qtd_bloq_csd,
            valor_carteira_csd=val_carteira_csd,
            total_colaterais=tot_colaterais,
            margem_requerida_total=tot_mrg_req,
            valor_avaliacao_total=tot_val_aval,
            margem_livre_total=mrg_livre_total,
            defasagem_total=defasagem_total,
            total_chamadas_margem=tot_chamadas,
            tem_chamada_margem=tem_chamada,
            imbarq004=f004,
            imbarq006=f006,
            imbarq011=f011,
            imbarq012=f012,
            imbarq014=f014,
        )

        self.last_summary = summary
        return summary

    def run(
        self,
        imbarq004: Optional[
            Union[Imbarq004File, str, Path, IO[str], Iterable[str]]
        ] = None,
        imbarq006: Optional[
            Union[Imbarq006File, str, Path, IO[str], Iterable[str]]
        ] = None,
        imbarq011: Optional[
            Union[Imbarq011File, str, Path, IO[str], Iterable[str]]
        ] = None,
        imbarq012: Optional[
            Union[Imbarq012File, str, Path, IO[str], Iterable[str]]
        ] = None,
        imbarq014: Optional[
            Union[Imbarq014File, str, Path, IO[str], Iterable[str]]
        ] = None,
        internal_positions: Optional[
            Iterable[Union[InternalPosition, Dict[str, Any]]]
        ] = None,
        data_movimento: Optional[Union[date, str]] = None,
        require_all_files: bool = True,
    ) -> DailyClosingSummary:
        """Alias para execute()."""
        return self.execute(
            imbarq004=imbarq004,
            imbarq006=imbarq006,
            imbarq011=imbarq011,
            imbarq012=imbarq012,
            imbarq014=imbarq014,
            internal_positions=internal_positions,
            data_movimento=data_movimento,
            require_all_files=require_all_files,
        )

    # -----------------------------------------------------------------------
    # Delegação dos Métodos de Exportação Estruturada
    # -----------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Exporta os dados consolidados do fechamento executado em dict."""
        if self.last_summary is None:
            raise RuntimeError(
                "O pipeline ainda não foi executado. Execute run() ou execute() primeiro."
            )
        return self.last_summary.to_dict()

    def to_json(
        self, output_path: Optional[Union[str, Path]] = None, indent: int = 2
    ) -> str:
        """Exporta os dados consolidados do fechamento executado em JSON."""
        if self.last_summary is None:
            raise RuntimeError(
                "O pipeline ainda não foi executado. Execute run() ou execute() primeiro."
            )
        return self.last_summary.to_json(output_path=output_path, indent=indent)

    def to_markdown_report(
        self, output_path: Optional[Union[str, Path]] = None
    ) -> str:
        """Exporta o relatório executivo de fechamento diário em Markdown."""
        if self.last_summary is None:
            raise RuntimeError(
                "O pipeline ainda não foi executado. Execute run() ou execute() primeiro."
            )
        return self.last_summary.to_markdown_report(output_path=output_path)
