"""Parser determinístico para o layout posicional IMBARQ014 (Garantias e Chamadas de Margem B3).

Este módulo implementa a leitura, validação estrutural e tipagem de relatórios
de garantias depositadas, margens requeridas e chamadas de margem (defasagem)
da Câmara da B3 (IMBARQ014 / FL-005), utilizando exclusivamente a biblioteca
padrão Python.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import IO, Dict, Iterable, List, Optional, Union

RECORD_LENGTH = 200
LAYOUT_ID = "IMBARQ014"
VALID_COLLATERAL_TYPES = ("MOE", "TPU", "ACO")
VALID_MARGIN_STATUSES = ("L", "D")


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
class CollateralMarginDetailRecord:
    """Registro de Detalhe (Tipo 01) — Garantia e Chamada de Margem."""
    tipo_registro: str
    codigo_participante: int
    codigo_conta_custodia: int
    tipo_colateral: str  # MOE, TPU, ACO
    identificador_ativo: str
    codigo_isin: str
    finalidade_garantia: str
    quantidade_depositada: int
    preco_avaliacao_unitario: Decimal
    valor_avaliacao: Decimal
    margem_requerida: Decimal
    situacao_margem: str  # L = Livre, D = Defasagem / Chamada de Margem
    valor_margem_livre_defasagem: Decimal
    participante_liquidacao: int
    linha_numero: int
    raw_line: str = field(repr=False)

    @property
    def is_chamada_margem(self) -> bool:
        """Indica se a posição está com chamada de margem / defasagem."""
        return self.situacao_margem == "D"

    @property
    def is_margem_livre(self) -> bool:
        """Indica se a posição possui margem livre / excedente."""
        return self.situacao_margem == "L"


@dataclass(frozen=True)
class TrailerRecord:
    """Registro Trailler (Tipo 99) — Totalização e Fechamento."""
    tipo_registro: str
    identificador_layout: str
    total_registros_arquivo: int
    total_registros_detalhe: int
    total_quantidade_depositada: int
    total_valor_avaliacao: Decimal
    total_margem_requerida: Decimal
    total_margem_livre_defasagem: Decimal
    raw_line: str = field(repr=False)


@dataclass(frozen=True)
class Imbarq014File:
    """Representação estruturada de um arquivo IMBARQ014 validado."""
    header: HeaderRecord
    detalhes: List[CollateralMarginDetailRecord]
    trailer: TrailerRecord

    @property
    def total_colaterais(self) -> int:
        """Quantidade de registros de garantias/margens presentes no arquivo."""
        return len(self.detalhes)

    def get_chamadas_margem(self) -> List[CollateralMarginDetailRecord]:
        """Retorna apenas os registros com chamada de margem (situação 'D')."""
        return [d for d in self.detalhes if d.is_chamada_margem]

    def get_margens_livres(self) -> List[CollateralMarginDetailRecord]:
        """Retorna apenas os registros com margem livre (situação 'L')."""
        return [d for d in self.detalhes if d.is_margem_livre]

    def get_garantias_por_tipo(self, tipo: str) -> List[CollateralMarginDetailRecord]:
        """Filtra registros por categoria de colateral ('MOE', 'TPU', 'ACO')."""
        return [d for d in self.detalhes if d.tipo_colateral == tipo.upper()]

    def get_posicoes_por_conta(self, conta: int) -> List[CollateralMarginDetailRecord]:
        """Filtra os registros por código de conta de custódia."""
        return [d for d in self.detalhes if d.codigo_conta_custodia == conta]

    def total_avaliacao_por_tipo(self) -> Dict[str, Decimal]:
        """Retorna o somatório do valor de avaliação agrupado por tipo de colateral."""
        totais: Dict[str, Decimal] = {}
        for d in self.detalhes:
            totais[d.tipo_colateral] = totais.get(d.tipo_colateral, Decimal("0.00")) + d.valor_avaliacao
        return totais

    def total_defasagem(self) -> Decimal:
        """Retorna o somatório de todas as chamadas de margem (defasagens) ativas."""
        return sum((d.valor_margem_livre_defasagem for d in self.detalhes if d.is_chamada_margem), Decimal("0.00"))


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

class Imbarq014Parser:
    """Parser determinístico para o layout IMBARQ014 (Garantias e Margens B3)."""

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

    def parse_line_detail(self, line: str, line_no: int) -> CollateralMarginDetailRecord:
        """Processa um registro de detalhe de garantia/margem (01)."""
        tipo = line[0:2]
        if tipo != "01":
            raise InvalidRecordStructureError(f"Linha {line_no} deveria ser Detalhe ('01'), mas encontrou {tipo!r}")

        cod_part = _parse_int(line[2:10], line_no, "codigo_participante")
        conta = _parse_int(line[10:22], line_no, "codigo_conta_custodia")
        tipo_colat = line[22:25].strip()
        ativo = line[25:37].strip()
        isin = line[37:49].strip()
        finalidade = line[49:52].strip()

        if tipo_colat not in VALID_COLLATERAL_TYPES:
            raise RecordParsingError(
                line_no, "tipo_colateral", tipo_colat,
                f"Tipo de colateral inválido. Esperado um de: {VALID_COLLATERAL_TYPES}"
            )
        if not ativo:
            raise RecordParsingError(line_no, "identificador_ativo", line[25:37], "Identificador do ativo ausente")
        if not isin:
            raise RecordParsingError(line_no, "codigo_isin", line[37:49], "Código ISIN ausente")

        qtd_dep = _parse_int(line[52:67], line_no, "quantidade_depositada")
        pu = _parse_decimal(line[67:82], 2, line_no, "preco_avaliacao_unitario")
        valor_aval = _parse_decimal(line[82:99], 2, line_no, "valor_avaliacao")
        mrg_req = _parse_decimal(line[99:116], 2, line_no, "margem_requerida")
        sit_mrg = line[116:117]

        if sit_mrg not in VALID_MARGIN_STATUSES:
            raise RecordParsingError(
                line_no, "situacao_margem", sit_mrg,
                f"Situação de margem inválida. Esperado 'L' (Livre) ou 'D' (Defasagem), obtido {sit_mrg!r}"
            )

        val_mrg_livre_def = _parse_decimal(line[117:134], 2, line_no, "valor_margem_livre_defasagem")
        pl = _parse_int(line[134:142], line_no, "participante_liquidacao")

        return CollateralMarginDetailRecord(
            tipo_registro=tipo,
            codigo_participante=cod_part,
            codigo_conta_custodia=conta,
            tipo_colateral=tipo_colat,
            identificador_ativo=ativo,
            codigo_isin=isin,
            finalidade_garantia=finalidade,
            quantidade_depositada=qtd_dep,
            preco_avaliacao_unitario=pu,
            valor_avaliacao=valor_aval,
            margem_requerida=mrg_req,
            situacao_margem=sit_mrg,
            valor_margem_livre_defasagem=val_mrg_livre_def,
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
        tot_qtd = _parse_int(line[27:45], line_no, "total_quantidade_depositada")
        tot_aval = _parse_decimal(line[45:63], 2, line_no, "total_valor_avaliacao")
        tot_req = _parse_decimal(line[63:81], 2, line_no, "total_margem_requerida")
        tot_livre_def = _parse_decimal(line[81:99], 2, line_no, "total_margem_livre_defasagem")

        return TrailerRecord(
            tipo_registro=tipo,
            identificador_layout=layout_id,
            total_registros_arquivo=tot_reg,
            total_registros_detalhe=tot_det,
            total_quantidade_depositada=tot_qtd,
            total_valor_avaliacao=tot_aval,
            total_margem_requerida=tot_req,
            total_margem_livre_defasagem=tot_livre_def,
            raw_line=line,
        )

    def parse_lines(self, lines: Iterable[str]) -> Imbarq014File:
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
        detalhes: List[CollateralMarginDetailRecord] = []
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

        calc_sum_qtd = sum(d.quantidade_depositada for d in detalhes)
        if trailer.total_quantidade_depositada != calc_sum_qtd:
            mismatches.append(
                f"Somatório de quantidade depositada diverge: trailler indica {trailer.total_quantidade_depositada}, "
                f"mas a soma calculada é {calc_sum_qtd}"
            )

        calc_sum_aval = sum((d.valor_avaliacao for d in detalhes), Decimal("0.00"))
        if trailer.total_valor_avaliacao != calc_sum_aval:
            mismatches.append(
                f"Somatório de valor de avaliação diverge: trailler indica {trailer.total_valor_avaliacao}, "
                f"mas a soma calculada é {calc_sum_aval}"
            )

        calc_sum_req = sum((d.margem_requerida for d in detalhes), Decimal("0.00"))
        if trailer.total_margem_requerida != calc_sum_req:
            mismatches.append(
                f"Somatório de margem requerida diverge: trailler indica {trailer.total_margem_requerida}, "
                f"mas a soma calculada é {calc_sum_req}"
            )

        calc_sum_livre_def = sum((d.valor_margem_livre_defasagem for d in detalhes), Decimal("0.00"))
        if trailer.total_margem_livre_defasagem != calc_sum_livre_def:
            mismatches.append(
                f"Somatório de margem livre/defasagem diverge: trailler indica {trailer.total_margem_livre_defasagem}, "
                f"mas a soma calculada é {calc_sum_livre_def}"
            )

        if mismatches:
            raise TrailerValidationError(mismatches)

        return Imbarq014File(header=header, detalhes=detalhes, trailer=trailer)

    def parse_file(self, file_path_or_buffer: Union[str, Path, IO[str]]) -> Imbarq014File:
        """Lê um arquivo do sistema de arquivos ou buffer e executa o parsing."""
        if hasattr(file_path_or_buffer, "readlines"):
            lines = file_path_or_buffer.readlines()  # type: ignore
            return self.parse_lines(lines)

        path = Path(file_path_or_buffer)
        with path.open("r", encoding="ascii", errors="replace") as f:
            return self.parse_lines(f)


def parse_imbarq014(source: Union[str, Path, IO[str], Iterable[str]]) -> Imbarq014File:
    """Função utilitária de conveniência para parsing do IMBARQ014."""
    parser = Imbarq014Parser()
    if isinstance(source, (str, Path)):
        p = Path(source)
        if p.exists() and p.is_file():
            return parser.parse_file(p)
        return parser.parse_lines(str(source).splitlines())
    if hasattr(source, "readlines"):
        return parser.parse_file(source)  # type: ignore
    return parser.parse_lines(source)
