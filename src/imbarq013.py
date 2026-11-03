"""Parser determinístico para o layout posicional IMBARQ013 (Movimentações da Depositária B3).

Este módulo implementa a leitura, validação estrutural, tipagem e reconciliação
do demonstrativo oficial de movimentações escriturais e extrato de custódia da Depositária
Central de Ativos Listados da B3 (IMBARQ013 / FL-010), utilizando exclusivamente
a biblioteca padrão Python.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import IO, Iterable, List, Optional, Tuple, Union

RECORD_LENGTH = 200
LAYOUT_ID = "IMBARQ013"

VALID_MOVEMENT_TYPES: Tuple[str, ...] = (
    "LIQ_PREGAO",
    "TRANSF_CUST",
    "BLOQ_JUD",
    "DESB_JUD",
    "DEPOS_GAR",
    "RETIR_GAR",
    "EXERC_OPC",
)

VALID_DC_INDICATORS: Tuple[str, ...] = ("D", "C")


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
    """Lançada quando a ordem ou estrutura canônica de registros for violada."""
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
    """Lançada quando os somatórios ou contagens do Trailler não batem com os registros de detalhe."""

    def __init__(self, mismatches: List[str]) -> None:
        self.mismatches = mismatches
        msg = "Falha de validação de integridade no Trailler (checksum divergente):\n - " + "\n - ".join(mismatches)
        super().__init__(msg)


# ---------------------------------------------------------------------------
# Modelos de Dados Tipados (Dataclasses)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HeaderRecord:
    """Registro Header (Tipo 00) — Controle de Abertura do IMBARQ013."""
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
class CustodyMovementRecord:
    """Registro de Detalhe (Tipo 01) — Movimentação / Extrato da Depositária de Listados."""
    tipo_registro: str
    numero_controle: int
    conta_custodia_origem: int
    conta_custodia_destino: int
    codigo_negociacao: str
    codigo_isin: str
    tipo_movimentacao: str
    data_movimento: date
    data_liquidacao_fisica: date
    indicador_debito_credito: str
    quantidade_movimentada: int
    motivo_historico: str
    linha_numero: int
    raw_line: str = field(repr=False)

    @property
    def is_debito(self) -> bool:
        """Indica se a movimentação é a débito da custódia."""
        return self.indicador_debito_credito == "D"

    @property
    def is_credito(self) -> bool:
        """Indica se a movimentação é a crédito da custódia."""
        return self.indicador_debito_credito == "C"


@dataclass(frozen=True)
class TrailerRecord:
    """Registro Trailler (Tipo 99) — Totalização e Fechamento do IMBARQ013."""
    tipo_registro: str
    identificador_layout: str
    total_registros_arquivo: int
    total_registros_detalhe: int
    total_quantidade_debito: int
    total_quantidade_credito: int
    sinal_saldo_liquido: str
    saldo_liquido_movimentacao: int
    raw_line: str = field(repr=False)


@dataclass(frozen=True)
class Imbarq013File:
    """Representação estruturada de um arquivo IMBARQ013 validado e conciliado."""
    header: HeaderRecord
    movimentacoes: List[CustodyMovementRecord]
    trailer: TrailerRecord

    @property
    def total_movimentacoes(self) -> int:
        """Quantidade de movimentações presentes no arquivo."""
        return len(self.movimentacoes)

    def filtrar_por_ticker(self, ticker: str) -> List[CustodyMovementRecord]:
        """Filtra as movimentações por código de negociação do ativo."""
        t_clean = ticker.strip().upper()
        return [m for m in self.movimentacoes if m.codigo_negociacao == t_clean]

    def filtrar_por_tipo(self, tipo_movimentacao: str) -> List[CustodyMovementRecord]:
        """Filtra as movimentações por tipo (ex: 'LIQ_PREGAO', 'BLOQ_JUD')."""
        tp_clean = tipo_movimentacao.strip().upper()
        return [m for m in self.movimentacoes if m.tipo_movimentacao == tp_clean]

    def filtrar_por_conta(self, conta: int) -> List[CustodyMovementRecord]:
        """Filtra as movimentações que envolvam a conta especificada (origem ou destino)."""
        return [m for m in self.movimentacoes if m.conta_custodia_origem == conta or m.conta_custodia_destino == conta]


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

class Imbarq013Parser:
    """Parser determinístico para o layout posicional IMBARQ013 (Movimentações da Depositária B3)."""

    def __init__(self, record_length: int = RECORD_LENGTH) -> None:
        self.record_length = record_length

    def parse_line_header(self, line: str, line_no: int = 1) -> HeaderRecord:
        """Processa e valida o registro Header (00)."""
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

    def parse_line_detail(self, line: str, line_no: int) -> CustodyMovementRecord:
        """Processa e valida um registro de movimentação de custódia (01)."""
        tipo = line[0:2]
        if tipo != "01":
            raise InvalidRecordStructureError(f"Linha {line_no} deveria ser Detalhe ('01'), mas encontrou {tipo!r}")

        num_controle = _parse_int(line[2:17], line_no, "numero_controle")
        conta_origem = _parse_int(line[17:29], line_no, "conta_custodia_origem")
        conta_destino = _parse_int(line[29:41], line_no, "conta_custodia_destino")
        ticker = line[41:53].rstrip()
        isin = line[53:65].rstrip()

        if not ticker:
            raise RecordParsingError(line_no, "codigo_negociacao", line[41:53], "Ticker obrigatório ausente")
        if not isin:
            raise RecordParsingError(line_no, "codigo_isin", line[53:65], "ISIN obrigatório ausente")

        tipo_mov = line[65:77].strip()
        if tipo_mov not in VALID_MOVEMENT_TYPES:
            raise RecordParsingError(
                line_no,
                "tipo_movimentacao",
                tipo_mov,
                f"Código de movimentação inválido. Esperado um de: {VALID_MOVEMENT_TYPES}"
            )

        dt_movimento = _parse_date(line[77:85], line_no, "data_movimento")
        dt_liq_fisica = _parse_date(line[85:93], line_no, "data_liquidacao_fisica")

        if dt_movimento is None:
            raise RecordParsingError(line_no, "data_movimento", line[77:85], "Data de movimento obrigatória")
        if dt_liq_fisica is None:
            raise RecordParsingError(line_no, "data_liquidacao_fisica", line[85:93], "Data de liquidação física obrigatória")

        if dt_liq_fisica < dt_movimento:
            raise RecordParsingError(
                line_no,
                "data_liquidacao_fisica",
                line[85:93],
                f"Data de liquidação física ({dt_liq_fisica}) anterior à data de movimento ({dt_movimento})"
            )

        ind_dc = line[93:94]
        if ind_dc not in VALID_DC_INDICATORS:
            raise RecordParsingError(
                line_no,
                "indicador_debito_credito",
                ind_dc,
                f"Indicador débito/crédito inválido. Esperado um de: {VALID_DC_INDICATORS}"
            )

        qtd_mov = _parse_int(line[94:109], line_no, "quantidade_movimentada")
        if qtd_mov < 0:
            raise RecordParsingError(
                line_no,
                "quantidade_movimentada",
                line[94:109],
                "Quantidade movimentada não pode ser negativa"
            )

        motivo_hist = line[109:149].rstrip()

        return CustodyMovementRecord(
            tipo_registro=tipo,
            numero_controle=num_controle,
            conta_custodia_origem=conta_origem,
            conta_custodia_destino=conta_destino,
            codigo_negociacao=ticker,
            codigo_isin=isin,
            tipo_movimentacao=tipo_mov,
            data_movimento=dt_movimento,
            data_liquidacao_fisica=dt_liq_fisica,
            indicador_debito_credito=ind_dc,
            quantidade_movimentada=qtd_mov,
            motivo_historico=motivo_hist,
            linha_numero=line_no,
            raw_line=line,
        )

    def parse_line_trailer(self, line: str, line_no: int) -> TrailerRecord:
        """Processa e valida o registro Trailler (99)."""
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
        tot_deb = _parse_int(line[27:45], line_no, "total_quantidade_debito")
        tot_cred = _parse_int(line[45:63], line_no, "total_quantidade_credito")

        sinal = line[63:64]
        if sinal not in ("C", "D"):
            raise RecordParsingError(
                line_no,
                "sinal_saldo_liquido",
                sinal,
                "Sinal do saldo líquido deve ser 'C' ou 'D'"
            )

        saldo_liq = _parse_int(line[64:82], line_no, "saldo_liquido_movimentacao")

        return TrailerRecord(
            tipo_registro=tipo,
            identificador_layout=layout_id,
            total_registros_arquivo=tot_reg,
            total_registros_detalhe=tot_det,
            total_quantidade_debito=tot_deb,
            total_quantidade_credito=tot_cred,
            sinal_saldo_liquido=sinal,
            saldo_liquido_movimentacao=saldo_liq,
            raw_line=line,
        )

    def parse_lines(self, lines: Iterable[str]) -> Imbarq013File:
        """Processa uma sequência de linhas de texto do IMBARQ013 e valida a integridade contábil."""
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

        # 1. Header (linha 1)
        header = self.parse_line_header(clean_lines[0], line_no=1)

        # 2. Detalhes (linhas 2 a N-1)
        movimentacoes: List[CustodyMovementRecord] = []
        for idx in range(1, len(clean_lines) - 1):
            line_no = idx + 1
            line = clean_lines[idx]
            mov = self.parse_line_detail(line, line_no=line_no)
            movimentacoes.append(mov)

        # 3. Trailler (última linha)
        trailer_line_no = len(clean_lines)
        trailer = self.parse_line_trailer(clean_lines[-1], line_no=trailer_line_no)

        # 4. Batimento Estrito de Integridade e Checksums do Trailler
        mismatches: List[str] = []

        total_lines_count = len(clean_lines)
        if trailer.total_registros_arquivo != total_lines_count:
            mismatches.append(
                f"Total de registros do arquivo diverge: trailler indica {trailer.total_registros_arquivo}, "
                f"mas contagem física é {total_lines_count}"
            )

        details_count = len(movimentacoes)
        if trailer.total_registros_detalhe != details_count:
            mismatches.append(
                f"Total de registros de detalhe diverge: trailler indica {trailer.total_registros_detalhe}, "
                f"mas contagem física de registros '01' é {details_count}"
            )

        calc_debito = sum(m.quantidade_movimentada for m in movimentacoes if m.indicador_debito_credito == "D")
        if trailer.total_quantidade_debito != calc_debito:
            mismatches.append(
                f"Somatório de quantidade a débito diverge: trailler indica {trailer.total_quantidade_debito}, "
                f"mas a soma calculada é {calc_debito}"
            )

        calc_credito = sum(m.quantidade_movimentada for m in movimentacoes if m.indicador_debito_credito == "C")
        if trailer.total_quantidade_credito != calc_credito:
            mismatches.append(
                f"Somatório de quantidade a crédito diverge: trailler indica {trailer.total_quantidade_credito}, "
                f"mas a soma calculada é {calc_credito}"
            )

        expected_sinal = "C" if calc_credito >= calc_debito else "D"
        if trailer.sinal_saldo_liquido != expected_sinal:
            mismatches.append(
                f"Sinal do saldo líquido diverge: trailler indica {trailer.sinal_saldo_liquido!r}, "
                f"mas o cálculo apurou {expected_sinal!r}"
            )

        calc_saldo_liquido = abs(calc_credito - calc_debito)
        if trailer.saldo_liquido_movimentacao != calc_saldo_liquido:
            mismatches.append(
                f"Saldo líquido da movimentação diverge: trailler indica {trailer.saldo_liquido_movimentacao}, "
                f"mas a diferença calculada (|C - D|) é {calc_saldo_liquido}"
            )

        if mismatches:
            raise TrailerValidationError(mismatches)

        return Imbarq013File(header=header, movimentacoes=movimentacoes, trailer=trailer)

    def parse_file(self, file_path_or_buffer: Union[str, Path, IO[str]]) -> Imbarq013File:
        """Lê arquivo do disco ou buffer de texto e executa o parsing determinístico."""
        if hasattr(file_path_or_buffer, "readlines"):
            lines = file_path_or_buffer.readlines()  # type: ignore
            return self.parse_lines(lines)

        path = Path(file_path_or_buffer)
        with path.open("r", encoding="ascii", errors="replace") as f:
            return self.parse_lines(f)


def parse_imbarq013(source: Union[str, Path, IO[str], Iterable[str]]) -> Imbarq013File:
    """Função utilitária de conveniência para parsing do arquivo IMBARQ013."""
    parser = Imbarq013Parser()
    if isinstance(source, (str, Path)):
        p = Path(source)
        if p.exists() and p.is_file():
            return parser.parse_file(p)
        return parser.parse_lines(str(source).splitlines())
    if hasattr(source, "readlines"):
        return parser.parse_file(source)  # type: ignore
    return parser.parse_lines(source)
