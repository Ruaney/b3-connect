"""Parser determinístico para o layout posicional IMBARQ018 (Eventos Corporativos Data Ex B3).

Este módulo implementa a leitura, validação posicional e tipagem de relatórios
de eventos corporativos e societários em data ex da B3 (IMBARQ018 / FL-006),
utilizando exclusivamente a biblioteca padrão Python.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import IO, Dict, Iterable, List, Optional, Union

RECORD_LENGTH = 200
LAYOUT_ID = "IMBARQ018"
VALID_EVENT_TYPES = ("DIVID", "JCP", "BONIF", "SUBSC", "DESD", "AGRUP")
VALID_TAX_INDICATORS = ("S", "N")
VALID_EVENT_STATUSES = ("APROV", "CANC", "PEND")


# ---------------------------------------------------------------------------
# Exceções Especializadas
# ---------------------------------------------------------------------------

class ImbarqError(Exception):
    """Exceção base para erros de processamento do layout IMBARQ."""
    pass


class InvalidRecordLengthError(ImbarqError):
    """Lançada quando uma linha não possui exatamente o comprimento esperado (200 caracteres)."""

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
    """Lançada quando ocorre falha na conversão tipada ou validação de domínio de um campo."""

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
class CorporateEventDetailRecord:
    """Registro de Detalhe (Tipo 01) — Evento Corporativo em Data Ex."""
    tipo_registro: str
    codigo_participante: int
    codigo_conta_custodia: int
    codigo_negociacao: str
    codigo_isin: str
    codigo_evento: str  # DIVID, JCP, BONIF, SUBSC, DESD, AGRUP
    data_com: date
    data_ex: date
    data_pagamento: Optional[date]
    valor_provento: Decimal  # N(13)V6
    percentual_evento: Decimal  # N(13)V4
    indicador_tributacao: str  # S ou N
    status_evento: str  # APROV, CANC, PEND
    linha_numero: int
    raw_line: str = field(repr=False)

    @property
    def ticker(self) -> str:
        """Alias para o ticker / código de negociação."""
        return self.codigo_negociacao

    @property
    def isin(self) -> str:
        """Alias para o código ISIN."""
        return self.codigo_isin

    @property
    def conta_custodia(self) -> int:
        """Alias para a conta de custódia do comitente."""
        return self.codigo_conta_custodia

    @property
    def is_tributado(self) -> bool:
        """Indica se o evento é tributado na fonte."""
        return self.indicador_tributacao == "S"

    @property
    def is_aprovado(self) -> bool:
        """Indica se o evento societário está aprovado."""
        return self.status_evento == "APROV"

    @property
    def is_cancelado(self) -> bool:
        """Indica se o evento societário foi cancelado."""
        return self.status_evento == "CANC"

    @property
    def is_pendente(self) -> bool:
        """Indica se o evento societário está pendente de homologação."""
        return self.status_evento == "PEND"

    @property
    def is_provento_em_dinheiro(self) -> bool:
        """Indica se o evento é um provento financeiro (dividendos ou JCP)."""
        return self.codigo_evento in ("DIVID", "JCP")

    @property
    def is_evento_acionario(self) -> bool:
        """Indica se o evento é societário em ações (bonificação, subscrição, split, reverse split)."""
        return self.codigo_evento in ("BONIF", "SUBSC", "DESD", "AGRUP")


@dataclass(frozen=True)
class TrailerRecord:
    """Registro Trailler (Tipo 99) — Totalização e Fechamento."""
    tipo_registro: str
    identificador_layout: str
    total_registros_arquivo: int
    total_registros_detalhe: int
    total_valor_proventos: Decimal
    raw_line: str = field(repr=False)


@dataclass(frozen=True)
class Imbarq018File:
    """Representação estruturada de um arquivo IMBARQ018 validado."""
    header: HeaderRecord
    detalhes: List[CorporateEventDetailRecord]
    trailer: TrailerRecord

    @property
    def total_eventos(self) -> int:
        """Quantidade de registros de detalhe de eventos corporativos presentes no arquivo."""
        return len(self.detalhes)

    def get_eventos_por_tipo(self, tipo: str) -> List[CorporateEventDetailRecord]:
        """Filtra eventos por código (ex: 'DIVID', 'JCP', 'BONIF')."""
        return [d for d in self.detalhes if d.codigo_evento == tipo.upper()]

    def get_eventos_por_ticker(self, ticker: str) -> List[CorporateEventDetailRecord]:
        """Filtra eventos por código de negociação / ticker."""
        return [d for d in self.detalhes if d.ticker == ticker.upper()]

    def get_eventos_por_conta(self, conta: int) -> List[CorporateEventDetailRecord]:
        """Filtra eventos por código da conta de custódia."""
        return [d for d in self.detalhes if d.codigo_conta_custodia == conta]

    def get_eventos_aprovados(self) -> List[CorporateEventDetailRecord]:
        """Filtra eventos societários aprovados."""
        return [d for d in self.detalhes if d.is_aprovado]

    def get_eventos_pendentes(self) -> List[CorporateEventDetailRecord]:
        """Filtra eventos societários pendentes."""
        return [d for d in self.detalhes if d.is_pendente]

    def get_eventos_cancelados(self) -> List[CorporateEventDetailRecord]:
        """Filtra eventos societários cancelados."""
        return [d for d in self.detalhes if d.is_cancelado]

    def total_proventos(self) -> Decimal:
        """Retorna o somatório dos valores de proventos por ação de todos os eventos."""
        return sum((d.valor_provento for d in self.detalhes), Decimal("0.000000"))

    def total_proventos_por_tipo(self) -> Dict[str, Decimal]:
        """Retorna o somatório dos valores de proventos agrupados por código de evento."""
        totais: Dict[str, Decimal] = {}
        for d in self.detalhes:
            totais[d.codigo_evento] = totais.get(d.codigo_evento, Decimal("0.000000")) + d.valor_provento
        return totais


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

class Imbarq018Parser:
    """Parser determinístico para o layout IMBARQ018 (Eventos Corporativos Data Ex B3)."""

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

    def parse_line_detail(self, line: str, line_no: int) -> CorporateEventDetailRecord:
        """Processa um registro de detalhe de evento corporativo (01)."""
        tipo = line[0:2]
        if tipo != "01":
            raise InvalidRecordStructureError(f"Linha {line_no} deveria ser Detalhe ('01'), mas encontrou {tipo!r}")

        cod_part = _parse_int(line[2:10], line_no, "codigo_participante")
        conta = _parse_int(line[10:22], line_no, "codigo_conta_custodia")
        ticker = line[22:34].strip()
        isin = line[34:46].strip()
        evento = line[46:52].strip()

        if not ticker:
            raise RecordParsingError(line_no, "codigo_negociacao", line[22:34], "Ticker / código de negociação ausente")
        if not isin:
            raise RecordParsingError(line_no, "codigo_isin", line[34:46], "Código ISIN ausente")
        if evento not in VALID_EVENT_TYPES:
            raise RecordParsingError(
                line_no, "codigo_evento", evento,
                f"Código de evento corporativo inválido. Esperado um de: {VALID_EVENT_TYPES}"
            )

        dt_com = _parse_date(line[52:60], line_no, "data_com")
        dt_ex = _parse_date(line[60:68], line_no, "data_ex")
        dt_pag = _parse_date(line[68:76], line_no, "data_pagamento", allow_zero=True)

        assert dt_com is not None
        assert dt_ex is not None

        valor_prov = _parse_decimal(line[76:95], 6, line_no, "valor_provento")
        perc_ev = _parse_decimal(line[95:112], 4, line_no, "percentual_evento")
        indicador_trib = line[112:113]

        if indicador_trib not in VALID_TAX_INDICATORS:
            raise RecordParsingError(
                line_no, "indicador_tributacao", indicador_trib,
                f"Indicador de tributação inválido. Esperado 'S' ou 'N', obtido {indicador_trib!r}"
            )

        status_ev = line[113:118].strip()
        if status_ev not in VALID_EVENT_STATUSES:
            raise RecordParsingError(
                line_no, "status_evento", status_ev,
                f"Status do evento inválido. Esperado um de: {VALID_EVENT_STATUSES}, obtido {status_ev!r}"
            )

        return CorporateEventDetailRecord(
            tipo_registro=tipo,
            codigo_participante=cod_part,
            codigo_conta_custodia=conta,
            codigo_negociacao=ticker,
            codigo_isin=isin,
            codigo_evento=evento,
            data_com=dt_com,
            data_ex=dt_ex,
            data_pagamento=dt_pag,
            valor_provento=valor_prov,
            percentual_evento=perc_ev,
            indicador_tributacao=indicador_trib,
            status_evento=status_ev,
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
        tot_prov = _parse_decimal(line[27:46], 6, line_no, "total_valor_proventos")

        return TrailerRecord(
            tipo_registro=tipo,
            identificador_layout=layout_id,
            total_registros_arquivo=tot_reg,
            total_registros_detalhe=tot_det,
            total_valor_proventos=tot_prov,
            raw_line=line,
        )

    def parse_lines(self, lines: Iterable[str]) -> Imbarq018File:
        """Processa uma sequência de linhas brutas e valida a integridade completa."""
        clean_lines: List[str] = []
        for idx, line in enumerate(lines, start=1):
            stripped = line.rstrip("\r\n")
            if not stripped:
                continue  # ignora linhas em branco no final
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
        detalhes: List[CorporateEventDetailRecord] = []
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

        calc_sum_prov = sum((d.valor_provento for d in detalhes), Decimal("0.000000"))
        if trailer.total_valor_proventos != calc_sum_prov:
            mismatches.append(
                f"Somatório de valor de proventos diverge: trailler indica {trailer.total_valor_proventos}, "
                f"mas a soma calculada é {calc_sum_prov}"
            )

        if mismatches:
            raise TrailerValidationError(mismatches)

        return Imbarq018File(header=header, detalhes=detalhes, trailer=trailer)

    def parse_file(self, file_path_or_buffer: Union[str, Path, IO[str]]) -> Imbarq018File:
        """Lê um arquivo do sistema de arquivos ou buffer e executa o parsing."""
        if hasattr(file_path_or_buffer, "readlines"):
            lines = file_path_or_buffer.readlines()  # type: ignore
            return self.parse_lines(lines)

        path = Path(file_path_or_buffer)
        with path.open("r", encoding="ascii", errors="replace") as f:
            return self.parse_lines(f)


def parse_imbarq018(source: Union[str, Path, IO[str], Iterable[str]]) -> Imbarq018File:
    """Função utilitária de conveniência para parsing do IMBARQ018."""
    parser = Imbarq018Parser()
    if isinstance(source, (str, Path)):
        p = Path(source)
        if p.exists() and p.is_file():
            return parser.parse_file(p)
        return parser.parse_lines(str(source).splitlines())
    if hasattr(source, "readlines"):
        return parser.parse_file(source)  # type: ignore
    return parser.parse_lines(source)
