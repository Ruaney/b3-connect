"""Parser determinístico para o layout posicional IMBARQ004 (Lançamentos Financeiros de Liquidação B3).

Este módulo implementa a leitura, validação estrutural e tipagem de arquivos
de lançamentos financeiros de liquidação da B3 (IMBARQ004 / FL-004),
utilizando exclusivamente a biblioteca padrão Python.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import IO, Iterable, List, Optional, Union

RECORD_LENGTH = 200
LAYOUT_ID = "IMBARQ004"


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
class FinancialPostingRecord:
    """Registro de Detalhe (Tipo 01) — Lançamento Financeiro de Liquidação."""
    tipo_registro: str
    codigo_participante: int
    codigo_conta_custodia: int
    numero_lancamento: int
    data_liquidacao: date
    codigo_historico: int
    descricao_historico: str
    natureza_lancamento: str  # 'C' ou 'D'
    valor_lancamento: Decimal
    codigo_negociacao: Optional[str]
    codigo_isin: Optional[str]
    tipo_mercado: Optional[str]
    participante_contraparte: int
    numero_documento: Optional[str]
    canal_liquidacao: str
    status_liquidacao: str  # 'L', 'P', 'C'
    linha_numero: int
    raw_line: str = field(repr=False)


@dataclass(frozen=True)
class TrailerRecord:
    """Registro Trailler (Tipo 99) — Totalização e Fechamento Contábil."""
    tipo_registro: str
    identificador_layout: str
    total_registros_arquivo: int
    total_registros_detalhe: int
    total_valor_debito: Decimal
    total_valor_credito: Decimal
    sinal_saldo_liquido: str  # 'C' ou 'D'
    total_saldo_liquido: Decimal
    raw_line: str = field(repr=False)


@dataclass(frozen=True)
class Imbarq004File:
    """Representação estruturada de um arquivo IMBARQ004 validado."""
    header: HeaderRecord
    detalhes: List[FinancialPostingRecord]
    trailer: TrailerRecord

    @property
    def total_lancamentos(self) -> int:
        """Quantidade de registros de detalhe presentes no arquivo."""
        return len(self.detalhes)

    @property
    def total_debitos(self) -> Decimal:
        """Somatório aritmético dos lançamentos de débito ('D')."""
        return sum((d.valor_lancamento for d in self.detalhes if d.natureza_lancamento == "D"), Decimal("0.00"))

    @property
    def total_creditos(self) -> Decimal:
        """Somatório aritmético dos lançamentos de crédito ('C')."""
        return sum((d.valor_lancamento for d in self.detalhes if d.natureza_lancamento == "C"), Decimal("0.00"))

    @property
    def saldo_liquido(self) -> Decimal:
        """Saldo financeiro líquido assinado (Créditos - Débitos)."""
        return self.total_creditos - self.total_debitos


# ---------------------------------------------------------------------------
# Utilitários de Conversão de Campos
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

class Imbarq004Parser:
    """Parser determinístico para o layout IMBARQ004."""

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

    def parse_line_detail(self, line: str, line_no: int) -> FinancialPostingRecord:
        """Processa um registro de detalhe (01) — Lançamento Financeiro."""
        tipo = line[0:2]
        if tipo != "01":
            raise InvalidRecordStructureError(f"Linha {line_no} deveria ser Detalhe ('01'), mas encontrou {tipo!r}")

        cod_part = _parse_int(line[2:10], line_no, "codigo_participante")
        conta = _parse_int(line[10:22], line_no, "codigo_conta_custodia")
        num_lanc = _parse_int(line[22:32], line_no, "numero_lancamento")
        dt_liq = _parse_date(line[32:40], line_no, "data_liquidacao")
        cod_hist = _parse_int(line[40:44], line_no, "codigo_historico")
        desc_hist = line[44:74].rstrip()
        natureza = line[74:75]

        if natureza not in ("C", "D"):
            raise RecordParsingError(line_no, "natureza_lancamento", natureza, "Natureza deve ser 'C' (Crédito) ou 'D' (Débito)")

        valor = _parse_decimal(line[75:92], 2, line_no, "valor_lancamento")
        ticker = line[92:104].strip() or None
        isin = line[104:116].strip() or None
        mercado = line[116:119].strip() or None
        contraparte = _parse_int(line[119:127], line_no, "participante_contraparte")
        num_doc = line[127:143].strip() or None
        canal = line[143:147].rstrip()
        status = line[147:148]

        if status not in ("L", "P", "C"):
            raise RecordParsingError(line_no, "status_liquidacao", status, "Status deve ser 'L' (Liquidado), 'P' (Pendente) ou 'C' (Cancelado)")

        assert dt_liq is not None

        return FinancialPostingRecord(
            tipo_registro=tipo,
            codigo_participante=cod_part,
            codigo_conta_custodia=conta,
            numero_lancamento=num_lanc,
            data_liquidacao=dt_liq,
            codigo_historico=cod_hist,
            descricao_historico=desc_hist,
            natureza_lancamento=natureza,
            valor_lancamento=valor,
            codigo_negociacao=ticker,
            codigo_isin=isin,
            tipo_mercado=mercado,
            participante_contraparte=contraparte,
            numero_documento=num_doc,
            canal_liquidacao=canal,
            status_liquidacao=status,
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
        tot_deb = _parse_decimal(line[27:45], 2, line_no, "total_valor_debito")
        tot_cred = _parse_decimal(line[45:63], 2, line_no, "total_valor_credito")
        sinal = line[63:64]

        if sinal not in ("C", "D"):
            raise RecordParsingError(line_no, "sinal_saldo_liquido", sinal, "Sinal do saldo deve ser 'C' ou 'D'")

        tot_liq = _parse_decimal(line[64:82], 2, line_no, "total_saldo_liquido")

        return TrailerRecord(
            tipo_registro=tipo,
            identificador_layout=layout_id,
            total_registros_arquivo=tot_reg,
            total_registros_detalhe=tot_det,
            total_valor_debito=tot_deb,
            total_valor_credito=tot_cred,
            sinal_saldo_liquido=sinal,
            total_saldo_liquido=tot_liq,
            raw_line=line,
        )

    def parse_lines(self, lines: Iterable[str]) -> Imbarq004File:
        """Processa uma sequência de linhas brutas e valida a integridade contábil completa."""
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

        # 2. Detalhes (linhas 2 até N-1)
        detalhes: List[FinancialPostingRecord] = []
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

        calc_debito = sum((d.valor_lancamento for d in detalhes if d.natureza_lancamento == "D"), Decimal("0.00"))
        if trailer.total_valor_debito != calc_debito:
            mismatches.append(
                f"Somatório de valor a débito diverge: trailler indica {trailer.total_valor_debito}, "
                f"mas a soma calculada é {calc_debito}"
            )

        calc_credito = sum((d.valor_lancamento for d in detalhes if d.natureza_lancamento == "C"), Decimal("0.00"))
        if trailer.total_valor_credito != calc_credito:
            mismatches.append(
                f"Somatório de valor a crédito diverge: trailler indica {trailer.total_valor_credito}, "
                f"mas a soma calculada é {calc_credito}"
            )

        expected_sinal = "C" if calc_credito >= calc_debito else "D"
        if trailer.sinal_saldo_liquido != expected_sinal:
            mismatches.append(
                f"Sinal do saldo líquido diverge: trailler indica {trailer.sinal_saldo_liquido!r}, "
                f"mas o cálculo apurou {expected_sinal!r}"
            )

        calc_saldo_liquido = abs(calc_credito - calc_debito)
        if trailer.total_saldo_liquido != calc_saldo_liquido:
            mismatches.append(
                f"Somatório do saldo líquido absoluto diverge: trailler indica {trailer.total_saldo_liquido}, "
                f"mas o cálculo apurou {calc_saldo_liquido}"
            )

        if mismatches:
            raise TrailerValidationError(mismatches)

        return Imbarq004File(header=header, detalhes=detalhes, trailer=trailer)

    def parse_file(self, file_path_or_buffer: Union[str, Path, IO[str]]) -> Imbarq004File:
        """Lê um arquivo do sistema de arquivos ou buffer e executa o parsing."""
        if hasattr(file_path_or_buffer, "readlines"):
            lines = file_path_or_buffer.readlines()  # type: ignore
            return self.parse_lines(lines)

        path = Path(file_path_or_buffer)
        with path.open("r", encoding="ascii", errors="replace") as f:
            return self.parse_lines(f)


def parse_imbarq004(source: Union[str, Path, IO[str], Iterable[str]]) -> Imbarq004File:
    """Função utilitária de conveniência para parsing do IMBARQ004."""
    parser = Imbarq004Parser()
    if isinstance(source, (str, Path)):
        p = Path(source)
        if p.exists() and p.is_file():
            return parser.parse_file(p)
        return parser.parse_lines(str(source).splitlines())
    if hasattr(source, "readlines"):
        return parser.parse_file(source)  # type: ignore
    return parser.parse_lines(source)
