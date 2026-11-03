"""Parser determinístico para o layout posicional IMBARQ006 (Posições da Câmara B3).

Este módulo implementa a leitura, validação estrutural e tipagem de arquivos
de posições em aberto da Câmara de Compensação e Liquidação da B3 (IMBARQ006),
utilizando exclusivamente a biblioteca padrão Python.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import IO, Iterable, List, Optional, Union

RECORD_LENGTH = 200
LAYOUT_ID = "IMBARQ006"


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


class TrailerValidationError(ImbarqError):
    """Lançada quando os somatórios ou contagens do Trailler não batem com os registros."""

    def __init__(self, mismatches: List[str]) -> None:
        self.mismatches = mismatches
        msg = "Falha de validação de integridade no Trailler (checksum divergente):\n - " + "\n - ".join(mismatches)
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
class PositionDetailRecord:
    """Registro de Detalhe (Tipo 01) — Posição em Aberto na Câmara B3."""
    tipo_registro: str
    codigo_participante: int
    codigo_conta_custodia: int
    codigo_negociacao: str
    codigo_isin: str
    tipo_mercado: str
    sentido_posicao: str
    quantidade_bruta: int
    quantidade_bloqueada: int
    quantidade_liquida: int
    preco_fechamento_ajuste: Decimal
    valor_financeiro: Decimal
    data_vencimento: Optional[date]
    codigo_serie: Optional[str]
    participante_liquidacao: int
    linha_numero: int
    raw_line: str = field(repr=False)


@dataclass(frozen=True)
class TrailerRecord:
    """Registro Trailler (Tipo 99) — Totalização e Fechamento."""
    tipo_registro: str
    identificador_layout: str
    total_registros_arquivo: int
    total_registros_detalhe: int
    total_quantidade_liquida: int
    total_valor_financeiro: Decimal
    raw_line: str = field(repr=False)


@dataclass(frozen=True)
class Imbarq006File:
    """Representação estruturada de um arquivo IMBARQ006 validado."""
    header: HeaderRecord
    detalhes: List[PositionDetailRecord]
    trailer: TrailerRecord

    @property
    def total_posicoes(self) -> int:
        """Quantidade de registros de detalhe presentes no arquivo."""
        return len(self.detalhes)


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

class Imbarq006Parser:
    """Parser determinístico para o layout IMBARQ006."""

    def __init__(self, record_length: int = RECORD_LENGTH) -> None:
        self.record_length = record_length

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

    def parse_line_detail(self, line: str, line_no: int) -> PositionDetailRecord:
        """Processa um registro de detalhe (01)."""
        tipo = line[0:2]
        if tipo != "01":
            raise InvalidRecordStructureError(f"Linha {line_no} deveria ser Detalhe ('01'), mas encontrou {tipo!r}")

        cod_part = _parse_int(line[2:10], line_no, "codigo_participante")
        conta = _parse_int(line[10:22], line_no, "codigo_conta_custodia")
        ticker = line[22:34].rstrip()
        isin = line[34:46].rstrip()
        mercado = line[46:49].rstrip()
        sentido = line[49:50]

        if not ticker:
            raise RecordParsingError(line_no, "codigo_negociacao", line[22:34], "Ticker obrigatório ausente")
        if not isin:
            raise RecordParsingError(line_no, "codigo_isin", line[34:46], "ISIN obrigatório ausente")
        if sentido not in ("C", "V", "D"):
            raise RecordParsingError(line_no, "sentido_posicao", sentido, "Sentido de posição inválido (esperado C/V)")

        q_bruta = _parse_int(line[50:65], line_no, "quantidade_bruta")
        q_bloq = _parse_int(line[65:80], line_no, "quantidade_bloqueada")
        q_liq = _parse_int(line[80:95], line_no, "quantidade_liquida")
        preco = _parse_decimal(line[95:110], 2, line_no, "preco_fechamento_ajuste")
        valor = _parse_decimal(line[110:127], 2, line_no, "valor_financeiro")
        dt_venc = _parse_date(line[127:135], line_no, "data_vencimento", allow_zero=True)
        serie = line[135:150].strip() or None
        pl = _parse_int(line[150:158], line_no, "participante_liquidacao")

        return PositionDetailRecord(
            tipo_registro=tipo,
            codigo_participante=cod_part,
            codigo_conta_custodia=conta,
            codigo_negociacao=ticker,
            codigo_isin=isin,
            tipo_mercado=mercado,
            sentido_posicao=sentido,
            quantidade_bruta=q_bruta,
            quantidade_bloqueada=q_bloq,
            quantidade_liquida=q_liq,
            preco_fechamento_ajuste=preco,
            valor_financeiro=valor,
            data_vencimento=dt_venc,
            codigo_serie=serie,
            participante_liquidacao=pl,
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
        tot_qliq = _parse_int(line[27:45], line_no, "total_quantidade_liquida")
        tot_vlr = _parse_decimal(line[45:63], 2, line_no, "total_valor_financeiro")

        return TrailerRecord(
            tipo_registro=tipo,
            identificador_layout=layout_id,
            total_registros_arquivo=tot_reg,
            total_registros_detalhe=tot_det,
            total_quantidade_liquida=tot_qliq,
            total_valor_financeiro=tot_vlr,
            raw_line=line,
        )

    def parse_lines(self, lines: Iterable[str]) -> Imbarq006File:
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
        detalhes: List[PositionDetailRecord] = []
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

        calc_sum_qliq = sum(d.quantidade_liquida for d in detalhes)
        if trailer.total_quantidade_liquida != calc_sum_qliq:
            mismatches.append(
                f"Somatório de quantidade líquida diverge: trailler indica {trailer.total_quantidade_liquida}, "
                f"mas a soma calculada é {calc_sum_qliq}"
            )

        calc_sum_vlr = sum((d.valor_financeiro for d in detalhes), Decimal("0.00"))
        if trailer.total_valor_financeiro != calc_sum_vlr:
            mismatches.append(
                f"Somatório de valor financeiro diverge: trailler indica {trailer.total_valor_financeiro}, "
                f"mas a soma calculada é {calc_sum_vlr}"
            )

        if mismatches:
            raise TrailerValidationError(mismatches)

        return Imbarq006File(header=header, detalhes=detalhes, trailer=trailer)

    def parse_file(self, file_path_or_buffer: Union[str, Path, IO[str]]) -> Imbarq006File:
        """Lê um arquivo do sistema de arquivos ou buffer e executa o parsing."""
        if hasattr(file_path_or_buffer, "readlines"):
            lines = file_path_or_buffer.readlines()  # type: ignore
            return self.parse_lines(lines)

        path = Path(file_path_or_buffer)
        with path.open("r", encoding="ascii", errors="replace") as f:
            return self.parse_lines(f)


def parse_imbarq006(source: Union[str, Path, IO[str], Iterable[str]]) -> Imbarq006File:
    """Função utilitária de conveniência para parsing do IMBARQ006."""
    parser = Imbarq006Parser()
    if isinstance(source, (str, Path)):
        p = Path(source)
        if p.exists() and p.is_file():
            return parser.parse_file(p)
        # Se for uma string de conteúdo
        return parser.parse_lines(str(source).splitlines())
    if hasattr(source, "readlines"):
        return parser.parse_file(source)  # type: ignore
    return parser.parse_lines(source)
