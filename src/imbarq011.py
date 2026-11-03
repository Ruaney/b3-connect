"""Parser determinístico para o layout posicional IMBARQ011 (Empréstimo de Ativos / BTC B3).

Este módulo implementa a leitura, validação estrutural e tipagem de arquivos
de posições de contratos de empréstimo de ativos do Banco de Títulos CBLC / B3 (IMBARQ011),
utilizando exclusivamente a biblioteca padrão Python.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import IO, Iterable, List, Optional, Union

RECORD_LENGTH = 200
LAYOUT_ID = "IMBARQ011"


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
class LoanContractRecord:
    """Registro de Detalhe (Tipo 01) — Contrato de Empréstimo de Ativos (BTC)."""
    tipo_registro: str
    numero_contrato: int
    codigo_participante_doador: int
    conta_doador: int
    codigo_participante_tomador: int
    conta_tomador: int
    codigo_negociacao: str
    codigo_isin: str
    quantidade_alugada: int
    taxa_aluguel: Decimal
    data_abertura: date
    data_vencimento: date
    tipo_reversibilidade: str
    preco_base: Decimal
    valor_contrato: Decimal
    linha_numero: int
    raw_line: str = field(repr=False)


@dataclass(frozen=True)
class TrailerRecord:
    """Registro Trailler (Tipo 99) — Totalização e Fechamento."""
    tipo_registro: str
    identificador_layout: str
    total_registros_arquivo: int
    total_registros_detalhe: int
    total_quantidade_alugada: int
    total_valor_contratos: Decimal
    raw_line: str = field(repr=False)


@dataclass(frozen=True)
class Imbarq011File:
    """Representação estruturada de um arquivo IMBARQ011 validado."""
    header: HeaderRecord
    contratos: List[LoanContractRecord]
    trailer: TrailerRecord

    @property
    def total_contratos(self) -> int:
        """Quantidade de contratos de empréstimo presentes no arquivo."""
        return len(self.contratos)


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

class Imbarq011Parser:
    """Parser determinístico para o layout IMBARQ011 (Empréstimo de Ativos / BTC)."""

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

    def parse_line_detail(self, line: str, line_no: int) -> LoanContractRecord:
        """Processa um registro de detalhe de Contrato de Empréstimo (01)."""
        tipo = line[0:2]
        if tipo != "01":
            raise InvalidRecordStructureError(f"Linha {line_no} deveria ser Detalhe ('01'), mas encontrou {tipo!r}")

        num_contrato = _parse_int(line[2:17], line_no, "numero_contrato")
        part_doador = _parse_int(line[17:25], line_no, "codigo_participante_doador")
        conta_doador = _parse_int(line[25:37], line_no, "conta_doador")
        part_tomador = _parse_int(line[37:45], line_no, "codigo_participante_tomador")
        conta_tomador = _parse_int(line[45:57], line_no, "conta_tomador")
        ticker = line[57:69].rstrip()
        isin = line[69:81].rstrip()

        if not ticker:
            raise RecordParsingError(line_no, "codigo_negociacao", line[57:69], "Ticker obrigatório ausente")
        if not isin:
            raise RecordParsingError(line_no, "codigo_isin", line[69:81], "ISIN obrigatório ausente")

        qtd_alugada = _parse_int(line[81:96], line_no, "quantidade_alugada")
        taxa = _parse_decimal(line[96:113], 4, line_no, "taxa_aluguel")
        dt_abertura = _parse_date(line[113:121], line_no, "data_abertura")
        dt_vencimento = _parse_date(line[121:129], line_no, "data_vencimento")

        if dt_abertura is None:
            raise RecordParsingError(line_no, "data_abertura", line[113:121], "Data de abertura obrigatória")
        if dt_vencimento is None:
            raise RecordParsingError(line_no, "data_vencimento", line[121:129], "Data de vencimento obrigatória")
        if dt_vencimento < dt_abertura:
            raise RecordParsingError(
                line_no,
                "data_vencimento",
                line[121:129],
                f"Data de vencimento ({dt_vencimento}) anterior à data de abertura ({dt_abertura})"
            )

        tipo_rev = line[129:130]
        if tipo_rev not in ("D", "T", "A", "N"):
            raise RecordParsingError(
                line_no,
                "tipo_reversibilidade",
                tipo_rev,
                "Tipo de reversibilidade inválido (esperado D, T, A ou N)"
            )

        preco_base = _parse_decimal(line[130:145], 2, line_no, "preco_base")
        valor_contrato = _parse_decimal(line[145:162], 2, line_no, "valor_contrato")

        return LoanContractRecord(
            tipo_registro=tipo,
            numero_contrato=num_contrato,
            codigo_participante_doador=part_doador,
            conta_doador=conta_doador,
            codigo_participante_tomador=part_tomador,
            conta_tomador=conta_tomador,
            codigo_negociacao=ticker,
            codigo_isin=isin,
            quantidade_alugada=qtd_alugada,
            taxa_aluguel=taxa,
            data_abertura=dt_abertura,
            data_vencimento=dt_vencimento,
            tipo_reversibilidade=tipo_rev,
            preco_base=preco_base,
            valor_contrato=valor_contrato,
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
        tot_qtd = _parse_int(line[27:45], line_no, "total_quantidade_alugada")
        tot_vlr = _parse_decimal(line[45:63], 2, line_no, "total_valor_contratos")

        return TrailerRecord(
            tipo_registro=tipo,
            identificador_layout=layout_id,
            total_registros_arquivo=tot_reg,
            total_registros_detalhe=tot_det,
            total_quantidade_alugada=tot_qtd,
            total_valor_contratos=tot_vlr,
            raw_line=line,
        )

    def parse_lines(self, lines: Iterable[str]) -> Imbarq011File:
        """Processa uma sequência de linhas brutas e valida a integridade completa."""
        clean_lines: List[str] = []
        for idx, line in enumerate(lines, start=1):
            stripped = line.rstrip("\r\n")
            if not stripped:
                continue
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
        contratos: List[LoanContractRecord] = []
        for idx in range(1, len(clean_lines) - 1):
            line_no = idx + 1
            line = clean_lines[idx]
            contrato = self.parse_line_detail(line, line_no=line_no)
            contratos.append(contrato)

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

        details_count = len(contratos)
        if trailer.total_registros_detalhe != details_count:
            mismatches.append(
                f"Total de registros de detalhe diverge: trailler indica {trailer.total_registros_detalhe}, "
                f"mas contagem física de registros '01' é {details_count}"
            )

        calc_sum_qtd = sum(c.quantidade_alugada for c in contratos)
        if trailer.total_quantidade_alugada != calc_sum_qtd:
            mismatches.append(
                f"Somatório de quantidade alugada diverge: trailler indica {trailer.total_quantidade_alugada}, "
                f"mas a soma calculada é {calc_sum_qtd}"
            )

        calc_sum_vlr = sum((c.valor_contrato for c in contratos), Decimal("0.00"))
        if trailer.total_valor_contratos != calc_sum_vlr:
            mismatches.append(
                f"Somatório de valor de contratos diverge: trailler indica {trailer.total_valor_contratos}, "
                f"mas a soma calculada é {calc_sum_vlr}"
            )

        if mismatches:
            raise TrailerValidationError(mismatches)

        return Imbarq011File(header=header, contratos=contratos, trailer=trailer)

    def parse_file(self, file_path_or_buffer: Union[str, Path, IO[str]]) -> Imbarq011File:
        """Lê um arquivo do sistema de arquivos ou buffer e executa o parsing."""
        if hasattr(file_path_or_buffer, "readlines"):
            lines = file_path_or_buffer.readlines()  # type: ignore
            return self.parse_lines(lines)

        path = Path(file_path_or_buffer)
        with path.open("r", encoding="ascii", errors="replace") as f:
            return self.parse_lines(f)


def parse_imbarq011(source: Union[str, Path, IO[str], Iterable[str]]) -> Imbarq011File:
    """Função utilitária de conveniência para parsing do IMBARQ011."""
    parser = Imbarq011Parser()
    if isinstance(source, (str, Path)):
        p = Path(source)
        if p.exists() and p.is_file():
            return parser.parse_file(p)
        return parser.parse_lines(str(source).splitlines())
    if hasattr(source, "readlines"):
        return parser.parse_file(source)  # type: ignore
    return parser.parse_lines(source)
