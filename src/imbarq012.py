"""Parser determinístico para o layout posicional IMBARQ012 (Posições da Depositária de Listados B3).

Este módulo implementa a leitura, validação estrutural, reconciliação de custódia
e tipagem de arquivos de posições em aberto e eventos societários mantidos na
Depositária Central de Ativos Listados da B3 (IMBARQ012), utilizando exclusivamente
a biblioteca padrão Python.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import IO, Iterable, List, Optional, Union

RECORD_LENGTH = 200
LAYOUT_ID = "IMBARQ012"


# ---------------------------------------------------------------------------
# Exceções Especializadas
# ---------------------------------------------------------------------------

class ImbarqError(Exception):
    """Exceção base para erros de processamento do layout IMBARQ."""
    pass


class InvalidRecordLengthError(ImbarqError):
    """Lançada quando uma linha não possui exatamente o comprimento esperado (200 chars)."""

    def __init__(self, line_number: int, expected_len: int, actual_len: int, raw_line: str) -> None:
        self.line_number = line_number
        self.expected_len = expected_len
        self.actual_len = actual_len
        self.raw_line = raw_line
        super().__init__(
            f"Linha {line_number} possui tamanho inválido: esperado {expected_len} colunas, "
            f"encontrado {actual_len} colunas. Conteúdo: {raw_line!r}"
        )


class InvalidRecordStructureError(ImbarqError):
    """Lançada quando a ordem ou estrutura de registros do arquivo for violada."""
    pass


class RecordParsingError(ImbarqError):
    """Lançada quando ocorre falha na conversão tipada de um campo específico."""

    def __init__(self, line_number: int, field_name: str, value: str, reason: str) -> None:
        self.line_number = line_number
        self.field_name = field_name
        self.value = value
        self.reason = reason
        super().__init__(
            f"Erro ao processar campo '{field_name}' na linha {line_number} "
            f"(valor: {value!r}): {reason}"
        )


class PositionConsistencyError(ImbarqError):
    """Lançada quando a reconciliação algébrica de saldos de custódia é violada."""

    def __init__(
        self,
        line_number: int,
        ticker: str,
        quantidade_total: int,
        quantidade_disponivel: int,
        quantidade_bloqueada_garantia: int,
        quantidade_bloqueada_judicial: int,
        reason: Optional[str] = None,
    ) -> None:
        self.line_number = line_number
        self.ticker = ticker
        self.quantidade_total = quantidade_total
        self.quantidade_disponivel = quantidade_disponivel
        self.quantidade_bloqueada_garantia = quantidade_bloqueada_garantia
        self.quantidade_bloqueada_judicial = quantidade_bloqueada_judicial
        soma_parcelas = (
            quantidade_disponivel + quantidade_bloqueada_garantia + quantidade_bloqueada_judicial
        )
        msg = (
            reason
            or f"Inconsistência de custódia na linha {line_number} (Ticker: {ticker}): "
            f"quantidade_total ({quantidade_total}) diverge da soma das parcelas "
            f"disponível + garantia + judicial ({quantidade_disponivel} + "
            f"{quantidade_bloqueada_garantia} + {quantidade_bloqueada_judicial} = {soma_parcelas})"
        )
        super().__init__(msg)


class TrailerValidationError(ImbarqError):
    """Lançada quando os somatórios ou contagens do Trailler não batem com os registros."""

    def __init__(self, mismatches: List[str]) -> None:
        self.mismatches = mismatches
        msg = (
            "Falha de validação de integridade no Trailler (checksum divergente):\n - "
            + "\n - ".join(mismatches)
        )
        super().__init__(msg)


# ---------------------------------------------------------------------------
# Modelos de Dados Tipados (Dataclasses)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HeaderRecord:
    """Registro Header (Tipo 00) — Controle de Abertura."""
    tipo_registro: str
    identificador_layout: str
    versao_layout: int
    data_geracao: date
    hora_geracao: time
    data_movimento: date
    codigo_participante: int
    nome_participante: str
    sequencial_remessa: int
    raw_line: str = field(repr=False)


@dataclass(frozen=True)
class DepositaryPositionRecord:
    """Registro de Detalhe (Tipo 01) — Posição em Custódia na Depositária B3."""
    tipo_registro: str
    codigo_participante: int
    codigo_conta_custodia: int
    codigo_negociacao: str
    codigo_isin: str
    tipo_mercado: str
    quantidade_total: int
    quantidade_disponivel: int
    quantidade_bloqueada_garantia: int
    quantidade_bloqueada_judicial: int
    quantidade_eventos_pendentes: int
    preco_fechamento: Decimal
    valor_financeiro: Decimal
    data_liquidacao_evento: Optional[date]
    tipo_evento: Optional[str]
    linha_numero: int
    raw_line: str = field(repr=False)

    @property
    def quantidade_bloqueada_total(self) -> int:
        """Soma de bloqueios para garantia e ordens judiciais."""
        return self.quantidade_bloqueada_garantia + self.quantidade_bloqueada_judicial

    @property
    def is_bloqueado(self) -> bool:
        """Indica se a posição possui algum saldo bloqueado."""
        return self.quantidade_bloqueada_total > 0

    @property
    def has_eventos_pendentes(self) -> bool:
        """Indica se há proventos ou eventos societários em trânsito."""
        return self.quantidade_eventos_pendentes > 0


@dataclass(frozen=True)
class TrailerRecord:
    """Registro Trailler (Tipo 99) — Totalização e Fechamento."""
    tipo_registro: str
    identificador_layout: str
    total_registros_arquivo: int
    total_registros_detalhe: int
    total_quantidade_total: int
    total_quantidade_disponivel: int
    total_quantidade_bloqueada: int
    total_eventos_pendentes: int
    total_valor_financeiro: Decimal
    raw_line: str = field(repr=False)


@dataclass(frozen=True)
class Imbarq012File:
    """Representação estruturada de um arquivo IMBARQ012 validado."""
    header: HeaderRecord
    detalhes: List[DepositaryPositionRecord]
    trailer: TrailerRecord

    @property
    def total_posicoes(self) -> int:
        """Quantidade de registros de detalhe presentes no arquivo."""
        return len(self.detalhes)

    @property
    def valor_total_carteira(self) -> Decimal:
        """Soma do valor financeiro de todas as posições em custódia."""
        return sum((d.valor_financeiro for d in self.detalhes), Decimal("0.00"))

    def get_posicoes_por_conta(self, conta: int) -> List[DepositaryPositionRecord]:
        """Filtra posições mantidas em determinada conta de custódia."""
        return [d for d in self.detalhes if d.codigo_conta_custodia == conta]

    def get_posicoes_por_ticker(self, ticker: str) -> List[DepositaryPositionRecord]:
        """Filtra posições por ticker do ativo."""
        target = ticker.strip().upper()
        return [d for d in self.detalhes if d.codigo_negociacao.upper() == target]

    def get_posicoes_por_isin(self, isin: str) -> List[DepositaryPositionRecord]:
        """Filtra posições por código ISIN."""
        target = isin.strip().upper()
        return [d for d in self.detalhes if d.codigo_isin.upper() == target]


# ---------------------------------------------------------------------------
# Utilitários de Parsing de Campos
# ---------------------------------------------------------------------------

def _parse_int(raw: str, line_no: int, field_name: str) -> int:
    val = raw.strip()
    if not val:
        raise RecordParsingError(line_no, field_name, raw, "Campo numérico inteiro vazio")
    try:
        return int(val)
    except ValueError as exc:
        raise RecordParsingError(line_no, field_name, raw, f"Não é um inteiro válido: {exc}") from exc


def _parse_decimal(raw: str, decimals: int, line_no: int, field_name: str) -> Decimal:
    val = raw.strip()
    if not val:
        raise RecordParsingError(line_no, field_name, raw, "Campo decimal vazio")
    if not val.isdigit():
        raise RecordParsingError(line_no, field_name, raw, "Caracteres não-numéricos em campo decimal implícito")
    if len(val) <= decimals:
        val = val.zfill(decimals + 1)
    int_part = val[:-decimals].lstrip("0") or "0"
    frac_part = val[-decimals:]
    return Decimal(f"{int_part}.{frac_part}")


def _parse_date(raw: str, line_no: int, field_name: str, allow_zero: bool = False) -> Optional[date]:
    val = raw.strip()
    if allow_zero and (val == "00000000" or not val or val == "0" * len(val)):
        return None
    try:
        return datetime.strptime(val, "%Y%m%d").date()
    except ValueError as exc:
        raise RecordParsingError(line_no, field_name, raw, f"Data inválida (esperado YYYYMMDD): {exc}") from exc


def _parse_time(raw: str, line_no: int, field_name: str) -> time:
    val = raw.strip()
    try:
        return datetime.strptime(val, "%H%M%S").time()
    except ValueError as exc:
        raise RecordParsingError(line_no, field_name, raw, f"Hora inválida (esperado HHMMSS): {exc}") from exc


# ---------------------------------------------------------------------------
# Parser Principal
# ---------------------------------------------------------------------------

class Imbarq012Parser:
    """Parser determinístico para o layout IMBARQ012 (Depositária B3)."""

    def __init__(self, record_length: int = RECORD_LENGTH, strict_consistency: bool = True) -> None:
        self.record_length = record_length
        self.strict_consistency = strict_consistency

    def parse_line_header(self, line: str, line_no: int = 1) -> HeaderRecord:
        """Processa o registro Header (00)."""
        tipo = line[0:2]
        if tipo != "00":
            raise InvalidRecordStructureError(f"Linha {line_no} deveria ser Header ('00'), mas encontrou {tipo!r}")

        layout_id = line[2:11].strip()
        if layout_id != LAYOUT_ID:
            raise InvalidRecordStructureError(
                f"Identificador de layout inválido na linha {line_no}: esperado {LAYOUT_ID!r}, obtido {layout_id!r}"
            )

        versao = _parse_int(line[11:14], line_no, "versao_layout")
        dt_geracao = _parse_date(line[14:22], line_no, "data_geracao")
        hr_geracao = _parse_time(line[22:28], line_no, "hora_geracao")
        dt_movimento = _parse_date(line[28:36], line_no, "data_movimento")
        cod_part = _parse_int(line[36:44], line_no, "codigo_participante")
        nome_part = line[44:74].rstrip()
        seq_remessa = _parse_int(line[74:80], line_no, "sequencial_remessa")

        assert dt_geracao is not None
        assert dt_movimento is not None

        return HeaderRecord(
            tipo_registro=tipo,
            identificador_layout=layout_id,
            versao_layout=versao,
            data_geracao=dt_geracao,
            hora_geracao=hr_geracao,
            data_movimento=dt_movimento,
            codigo_participante=cod_part,
            nome_participante=nome_part,
            sequencial_remessa=seq_remessa,
            raw_line=line,
        )

    def parse_line_detail(self, line: str, line_no: int) -> DepositaryPositionRecord:
        """Processa um registro de detalhe da depositária (01)."""
        tipo = line[0:2]
        if tipo != "01":
            raise InvalidRecordStructureError(f"Linha {line_no} deveria ser Detalhe ('01'), mas encontrou {tipo!r}")

        cod_part = _parse_int(line[2:10], line_no, "codigo_participante")
        conta = _parse_int(line[10:22], line_no, "codigo_conta_custodia")
        ticker = line[22:34].rstrip()
        isin = line[34:46].rstrip()
        mercado = line[46:49].rstrip()

        if not ticker:
            raise RecordParsingError(line_no, "codigo_negociacao", line[22:34], "Ticker obrigatório ausente")
        if not isin:
            raise RecordParsingError(line_no, "codigo_isin", line[34:46], "ISIN obrigatório ausente")

        q_tot = _parse_int(line[49:64], line_no, "quantidade_total")
        q_disp = _parse_int(line[64:79], line_no, "quantidade_disponivel")
        q_bloq_gar = _parse_int(line[79:94], line_no, "quantidade_bloqueada_garantia")
        q_bloq_jud = _parse_int(line[94:109], line_no, "quantidade_bloqueada_judicial")
        q_ev_pend = _parse_int(line[109:124], line_no, "quantidade_eventos_pendentes")

        preco = _parse_decimal(line[124:139], 2, line_no, "preco_fechamento")
        valor = _parse_decimal(line[139:156], 2, line_no, "valor_financeiro")
        dt_liq_ev = _parse_date(line[156:164], line_no, "data_liquidacao_evento", allow_zero=True)
        tp_evento = line[164:172].strip() or None

        # Validação de consistência estrita dos saldos de custódia
        if self.strict_consistency:
            if q_tot < 0 or q_disp < 0 or q_bloq_gar < 0 or q_bloq_jud < 0 or q_ev_pend < 0:
                raise PositionConsistencyError(
                    line_no, ticker, q_tot, q_disp, q_bloq_gar, q_bloq_jud,
                    reason=f"Quantidades de custódia não podem ser negativas na linha {line_no}"
                )

            soma_parcelas = q_disp + q_bloq_gar + q_bloq_jud
            if q_tot != soma_parcelas:
                raise PositionConsistencyError(
                    line_no, ticker, q_tot, q_disp, q_bloq_gar, q_bloq_jud
                )

        return DepositaryPositionRecord(
            tipo_registro=tipo,
            codigo_participante=cod_part,
            codigo_conta_custodia=conta,
            codigo_negociacao=ticker,
            codigo_isin=isin,
            tipo_mercado=mercado,
            quantidade_total=q_tot,
            quantidade_disponivel=q_disp,
            quantidade_bloqueada_garantia=q_bloq_gar,
            quantidade_bloqueada_judicial=q_bloq_jud,
            quantidade_eventos_pendentes=q_ev_pend,
            preco_fechamento=preco,
            valor_financeiro=valor,
            data_liquidacao_evento=dt_liq_ev,
            tipo_evento=tp_evento,
            linha_numero=line_no,
            raw_line=line,
        )

    def parse_line_trailer(self, line: str, line_no: int) -> TrailerRecord:
        """Processa o registro Trailler (99)."""
        tipo = line[0:2]
        if tipo != "99":
            raise InvalidRecordStructureError(f"Linha {line_no} deveria ser Trailler ('99'), mas encontrou {tipo!r}")

        layout_id = line[2:11].strip()
        if layout_id != LAYOUT_ID:
            raise InvalidRecordStructureError(
                f"Identificador de layout inválido no Trailler na linha {line_no}: esperado {LAYOUT_ID!r}, obtido {layout_id!r}"
            )

        tot_reg = _parse_int(line[11:19], line_no, "total_registros_arquivo")
        tot_det = _parse_int(line[19:27], line_no, "total_registros_detalhe")
        tot_q_tot = _parse_int(line[27:45], line_no, "total_quantidade_total")
        tot_q_disp = _parse_int(line[45:63], line_no, "total_quantidade_disponivel")
        tot_q_bloq = _parse_int(line[63:81], line_no, "total_quantidade_bloqueada")
        tot_ev_pend = _parse_int(line[81:99], line_no, "total_eventos_pendentes")
        tot_vlr = _parse_decimal(line[99:117], 2, line_no, "total_valor_financeiro")

        return TrailerRecord(
            tipo_registro=tipo,
            identificador_layout=layout_id,
            total_registros_arquivo=tot_reg,
            total_registros_detalhe=tot_det,
            total_quantidade_total=tot_q_tot,
            total_quantidade_disponivel=tot_q_disp,
            total_quantidade_bloqueada=tot_q_bloq,
            total_eventos_pendentes=tot_ev_pend,
            total_valor_financeiro=tot_vlr,
            raw_line=line,
        )

    def parse_lines(self, lines: Iterable[str]) -> Imbarq012File:
        """Processa uma sequência de linhas brutas e valida a integridade completa."""
        clean_lines: List[str] = []
        for idx, line in enumerate(lines, start=1):
            stripped = line.rstrip("\r\n")
            if not stripped:
                continue  # ignora linhas totalmente em branco no final
            if len(stripped) != self.record_length:
                raise InvalidRecordLengthError(
                    line_number=idx,
                    expected_len=self.record_length,
                    actual_len=len(stripped),
                    raw_line=stripped,
                )
            clean_lines.append(stripped)

        if len(clean_lines) < 2:
            raise InvalidRecordStructureError(
                f"Arquivo possui menos linhas que o mínimo necessário ({len(clean_lines)} linhas). "
                "Exige-se ao menos 1 Header e 1 Trailler."
            )

        # 1. Header
        header = self.parse_line_header(clean_lines[0], line_no=1)

        # 2. Detalhes (linhas 2 até len-1)
        detalhes: List[DepositaryPositionRecord] = []
        for idx in range(1, len(clean_lines) - 1):
            line_no = idx + 1
            line = clean_lines[idx]
            detalhe = self.parse_line_detail(line, line_no=line_no)
            detalhes.append(detalhe)

        # 3. Trailler (última linha)
        trailer_line_no = len(clean_lines)
        trailer = self.parse_line_trailer(clean_lines[-1], line_no=trailer_line_no)

        # 4. Validação Rigorosa de Cheque do Trailler
        mismatches: List[str] = []

        total_lines_count = len(clean_lines)
        if trailer.total_registros_arquivo != total_lines_count:
            mismatches.append(
                f"Total de registros do arquivo diverge: trailler indica {trailer.total_registros_arquivo}, "
                f"mas contagem física é {total_lines_count}"
            )

        details_count = len(detalhes)
        if trailer.total_registros_detalhe != details_count:
            mismatches.append(
                f"Total de registros de detalhe diverge: trailler indica {trailer.total_registros_detalhe}, "
                f"mas contagem física de registros '01' é {details_count}"
            )

        calc_sum_qtot = sum(d.quantidade_total for d in detalhes)
        if trailer.total_quantidade_total != calc_sum_qtot:
            mismatches.append(
                f"Somatório de quantidade total diverge: trailler indica {trailer.total_quantidade_total}, "
                f"mas a soma calculada é {calc_sum_qtot}"
            )

        calc_sum_qdisp = sum(d.quantidade_disponivel for d in detalhes)
        if trailer.total_quantidade_disponivel != calc_sum_qdisp:
            mismatches.append(
                f"Somatório de quantidade disponível diverge: trailler indica {trailer.total_quantidade_disponivel}, "
                f"mas a soma calculada é {calc_sum_qdisp}"
            )

        calc_sum_qbloq = sum(d.quantidade_bloqueada_total for d in detalhes)
        if trailer.total_quantidade_bloqueada != calc_sum_qbloq:
            mismatches.append(
                f"Somatório de quantidade bloqueada diverge: trailler indica {trailer.total_quantidade_bloqueada}, "
                f"mas a soma calculada é {calc_sum_qbloq}"
            )

        calc_sum_evpend = sum(d.quantidade_eventos_pendentes for d in detalhes)
        if trailer.total_eventos_pendentes != calc_sum_evpend:
            mismatches.append(
                f"Somatório de eventos pendentes diverge: trailler indica {trailer.total_eventos_pendentes}, "
                f"mas a soma calculada é {calc_sum_evpend}"
            )

        calc_sum_vlr = sum((d.valor_financeiro for d in detalhes), Decimal("0.00"))
        if trailer.total_valor_financeiro != calc_sum_vlr:
            mismatches.append(
                f"Somatório de valor financeiro diverge: trailler indica {trailer.total_valor_financeiro}, "
                f"mas a soma calculada é {calc_sum_vlr}"
            )

        if mismatches:
            raise TrailerValidationError(mismatches)

        return Imbarq012File(header=header, detalhes=detalhes, trailer=trailer)

    def parse_file(self, file_path_or_buffer: Union[str, Path, IO[str]]) -> Imbarq012File:
        """Lê um arquivo do sistema de arquivos ou buffer e executa o parsing."""
        if hasattr(file_path_or_buffer, "readlines"):
            lines = file_path_or_buffer.readlines()  # type: ignore
            return self.parse_lines(lines)

        path = Path(file_path_or_buffer)
        with path.open("r", encoding="ascii", errors="replace") as f:
            return self.parse_lines(f)


def parse_imbarq012(
    source: Union[str, Path, IO[str], Iterable[str]],
    strict_consistency: bool = True,
) -> Imbarq012File:
    """Função utilitária de conveniência para parsing do IMBARQ012."""
    parser = Imbarq012Parser(strict_consistency=strict_consistency)
    if isinstance(source, (str, Path)):
        p = Path(source)
        if p.exists() and p.is_file():
            return parser.parse_file(p)
        # Se for uma string de conteúdo
        return parser.parse_lines(str(source).splitlines())
    if hasattr(source, "readlines"):
        return parser.parse_file(source)  # type: ignore
    return parser.parse_lines(source)
