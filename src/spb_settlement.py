"""Módulo de Integração com o SPB e Liquidação Multilateral B3 (FL-027).

Este módulo implementa a mensageria padronizada do Catálogo de Mensagens
do Sistema de Pagamentos Brasileiro (SPB / Banco Central do Brasil) para
liquidação financeira multilateral da Câmara B3 (B3 Clearing).

Abrange:
- Mensagem LDL0001 (Aviso de Débito/Crédito de Liquidação Multilateral).
- Mensagem STR0010 (Transferência de Fundos para Cobertura de Débitos da Câmara).
- Validação estrita de ISPB de 8 dígitos e tags de controle financeiro.
- Motor determinístico de conciliação cruzada com o IMBARQ004 (FL-004).
- Zero dependências externas (exclusivamente biblioteca padrão Python).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import io
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union
import xml.etree.ElementTree as ET

# Identificador padrão da B3 Clearing no SPB
ISPB_B3_DEFAULT = "00997185"
ISPB_CETIP_DEFAULT = "28719664"
DOMINIO_SISTEMA_SPB = "SPB01"
XML_NS_LDL0001 = "http://www.bcb.gov.br/SPB/LDL0001.xsd"
XML_NS_STR0010 = "http://www.bcb.gov.br/SPB/STR0010.xsd"


# ---------------------------------------------------------------------------
# Exceções de Domínio SPB
# ---------------------------------------------------------------------------

class SpbError(Exception):
    """Exceção base para operações do Sistema de Pagamentos Brasileiro."""
    pass


class InvalidIspbError(SpbError):
    """Lançada quando um código ISPB não cumpre a regra estrita de 8 dígitos numéricos."""
    pass


class SpbXmlValidationError(SpbError):
    """Lançada quando uma mensagem XML do SPB viola o esquema ou regras de validação."""
    pass


class SpbReconciliationMismatchError(SpbError):
    """Lançada quando há divergência contábil entre IMBARQ004 e a mensageria SPB."""
    pass


# ---------------------------------------------------------------------------
# Validação e Utilitários de Domínio
# ---------------------------------------------------------------------------

_ISPB_REGEX = re.compile(r"^\d{8}$")


def validate_ispb(ispb: Any, field_name: str = "ISPB") -> str:
    """Valida se o código ISPB possui rigorosamente 8 dígitos numéricos decimais.

    Args:
        ispb: Valor do ISPB (string de 8 dígitos ou inteiro no intervalo 0..99999999).
        field_name: Nome do campo para contexto em mensagens de erro.

    Returns:
        String de exatamente 8 dígitos numéricos (com preenchimento de zeros à esquerda se int).

    Raises:
        InvalidIspbError: Se o ISPB for inválido, nulo ou não contiver 8 dígitos.
    """
    if ispb is None:
        raise InvalidIspbError(f"Campo {field_name} não pode ser nulo.")

    if isinstance(ispb, int):
        if not (0 <= ispb <= 99999999):
            raise InvalidIspbError(
                f"Campo {field_name} inteiro fora do intervalo válido (0 a 99999999): {ispb}"
            )
        return f"{ispb:08d}"

    if not isinstance(ispb, str):
        raise InvalidIspbError(
            f"Campo {field_name} deve ser string ou int, recebido tipo {type(ispb).__name__}."
        )

    # String com espaços em branco internos ou nas bordas não é um ISPB válido estrito
    if not _ISPB_REGEX.match(ispb):
        raise InvalidIspbError(
            f"Campo {field_name} inválido: esperado exatamente 8 dígitos numéricos, recebido {ispb!r}."
        )

    return ispb


def _strip_ns(tag: str) -> str:
    """Remove o namespace XML do nome da tag ({http:...}Tag -> Tag)."""
    return tag.split("}", 1)[1] if "}" in tag else tag


def _find_child_elem(parent: ET.Element, tag_name: str) -> Optional[ET.Element]:
    """Localiza elemento filho ignorando prefixo de namespace XML."""
    for child in parent:
        if _strip_ns(child.tag) == tag_name:
            return child
    return None


def _get_required_text(parent: ET.Element, tag_name: str, context: str = "") -> str:
    """Extrai texto obrigatório de um elemento filho ignorando namespace."""
    elem = _find_child_elem(parent, tag_name)
    if elem is None or elem.text is None or not elem.text.strip():
        ctx = f" em {context}" if context else ""
        raise SpbXmlValidationError(f"Tag obrigatória <{tag_name}> não encontrada ou vazia{ctx}.")
    return elem.text.strip()


def _get_optional_text(parent: ET.Element, tag_name: str) -> Optional[str]:
    """Extrai texto opcional de um elemento filho ignorando namespace."""
    elem = _find_child_elem(parent, tag_name)
    if elem is None or elem.text is None:
        return None
    val = elem.text.strip()
    return val if val else None


def _parse_xml_date(date_str: str, tag_name: str) -> date:
    """Converte string YYYY-MM-DD em date."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError as exc:
        raise SpbXmlValidationError(
            f"Data inválida na tag <{tag_name}>: esperado formato YYYY-MM-DD, recebido {date_str!r}."
        ) from exc


def _parse_xml_decimal(val_str: str, tag_name: str) -> Decimal:
    """Converte string monetária em Decimal estrito positivo."""
    try:
        val = Decimal(val_str)
    except (InvalidOperation, ValueError) as exc:
        raise SpbXmlValidationError(
            f"Valor numérico inválido na tag <{tag_name}>: {val_str!r}."
        ) from exc

    if val <= Decimal("0.00"):
        raise SpbXmlValidationError(
            f"Valor na tag <{tag_name}> deve ser estritamente positivo (> 0), recebido: {val}."
        )
    return val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Dataclasses de Domínio
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SpbMessageHeader:
    """Segmento de Controle BCMSG (Banco Central Message)."""
    emissor_ispb: str
    destinatario_ispb: str
    dominio_sistema: str = DOMINIO_SISTEMA_SPB
    nu_op: str = ""
    identd_contg: str = "N"
    identd_operad: Optional[str] = None
    num_seq: int = 1
    indr_cont: str = "N"

    def __post_init__(self) -> None:
        object.__setattr__(self, "emissor_ispb", validate_ispb(self.emissor_ispb, "IdentdEmissor"))
        object.__setattr__(self, "destinatario_ispb", validate_ispb(self.destinatario_ispb, "IdentdDestinatario"))
        if not self.dominio_sistema:
            raise SpbXmlValidationError("Tag <DomSist> não pode ser vazia.")
        if not self.nu_op:
            raise SpbXmlValidationError("Tag <NUOp> não pode ser vazia.")


@dataclass(frozen=True)
class LdlSettlementRecord:
    """Segmento SISMSG / LDL0001 — Aviso de Débito/Crédito de Liquidação Multilateral."""
    num_ctrl_ldl: str
    num_ctrl_str: str
    ispb_camara: str
    ispb_participante: str
    tipo_debito_credito: str  # 'C' (Crédito) ou 'D' (Débito)
    valor_liquidacao: Decimal
    data_liquidacao: date
    numero_conta: str
    cod_msg: str = "LDL0001"
    tipo_conta: str = "CC"
    agencia: Optional[str] = None
    codigo_camara: str = "B3"

    def __post_init__(self) -> None:
        object.__setattr__(self, "ispb_camara", validate_ispb(self.ispb_camara, "ISPBCamara"))
        object.__setattr__(self, "ispb_participante", validate_ispb(self.ispb_participante, "ISPBIF"))

        if self.cod_msg != "LDL0001":
            raise SpbXmlValidationError(f"Código de mensagem inválido para LDL0001: {self.cod_msg!r}")

        if self.tipo_debito_credito not in ("C", "D"):
            raise SpbXmlValidationError(
                f"Tipo de lançamento inválido: esperado 'C' ou 'D', recebido {self.tipo_debito_credito!r}"
            )

        if not isinstance(self.valor_liquidacao, Decimal):
            try:
                dec_val = Decimal(str(self.valor_liquidacao))
            except Exception as e:
                raise SpbXmlValidationError(f"Valor de liquidação inválido: {self.valor_liquidacao}") from e
            object.__setattr__(self, "valor_liquidacao", dec_val)

        if self.valor_liquidacao <= Decimal("0.00"):
            raise SpbXmlValidationError(
                f"Valor de liquidação deve ser estritamente positivo, recebido: {self.valor_liquidacao}"
            )

        if not self.num_ctrl_ldl:
            raise SpbXmlValidationError("Campo <NumCtrlLDL> é obrigatório.")
        if not self.num_ctrl_str:
            raise SpbXmlValidationError("Campo <NumCtrlSTR> é obrigatório.")
        if not self.numero_conta:
            raise SpbXmlValidationError("Campo <NumCt> é obrigatório.")


@dataclass(frozen=True)
class StrTransferRecord:
    """Segmento SISMSG / STR0010 — Transferência de Fundos para Cobertura de Débitos da Câmara."""
    num_ctrl_if: str
    ispb_if_debitada: str
    ispb_if_creditada: str
    valor_transferencia: Decimal
    data_agendamento: date
    numero_conta_debitada: str
    numero_conta_creditada: str
    cod_msg: str = "STR0010"
    num_ctrl_str: Optional[str] = None
    tipo_conta_debitada: str = "RESERVAS"
    tipo_conta_creditada: str = "LIQUIDACAO"
    finalidade: str = "LIQUIDACAO MULTILATERAL B3 CLEARING"

    def __post_init__(self) -> None:
        object.__setattr__(self, "ispb_if_debitada", validate_ispb(self.ispb_if_debitada, "ISPBIFDebtd"))
        object.__setattr__(self, "ispb_if_creditada", validate_ispb(self.ispb_if_creditada, "ISPBIFCredtd"))

        if self.cod_msg != "STR0010":
            raise SpbXmlValidationError(f"Código de mensagem inválido para STR0010: {self.cod_msg!r}")

        if not isinstance(self.valor_transferencia, Decimal):
            try:
                dec_val = Decimal(str(self.valor_transferencia))
            except Exception as e:
                raise SpbXmlValidationError(f"Valor de transferência inválido: {self.valor_transferencia}") from e
            object.__setattr__(self, "valor_transferencia", dec_val)

        if self.valor_transferencia <= Decimal("0.00"):
            raise SpbXmlValidationError(
                f"Valor de transferência deve ser positivo, recebido: {self.valor_transferencia}"
            )

        if not self.num_ctrl_if:
            raise SpbXmlValidationError("Campo <NumCtrlIF> é obrigatório.")


@dataclass(frozen=True)
class SpbSettlementMessage:
    """Envelope completo de mensagem SPB (Header + Registro Funcional)."""
    header: SpbMessageHeader
    record: Union[LdlSettlementRecord, StrTransferRecord]
    raw_xml: Optional[str] = field(default=None, repr=False)


@dataclass(frozen=True)
class SettlementReconciliationResult:
    """Resultado do batimento determinístico entre IMBARQ004 e a mensageria SPB."""
    is_reconciled: bool
    status: str  # MATCHED, MISMATCH_AMOUNT, MISMATCH_DIRECTION, MISMATCH_DATE, MISMATCH_PARTICIPANT
    imbarq004_saldo_liquido: Decimal
    imbarq004_natureza: str  # 'C' ou 'D'
    spb_valor: Decimal
    spb_natureza: str  # 'C' ou 'D'
    diff_valor: Decimal
    detalhes: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Motor de Mensageria e Liquidação SPB
# ---------------------------------------------------------------------------

class SpbSettlementEngine:
    """Motor de processamento de mensageria SPB e conciliação com a câmara B3."""

    def __init__(self, ispb_camara: str = ISPB_B3_DEFAULT) -> None:
        self.ispb_camara = validate_ispb(ispb_camara, "ispb_camara")

    # -----------------------------------------------------------------------
    # Parsing de Mensagens XML
    # -----------------------------------------------------------------------

    def parse_spb_xml(self, source: Union[str, bytes, Path, io.IOBase]) -> ET.Element:
        """Carrega e valida a raiz <DOC> de um XML do Catálogo SPB."""
        raw_text = ""
        if isinstance(source, Path):
            with open(source, "r", encoding="utf-8") as f:
                raw_text = f.read()
        elif isinstance(source, (str, bytes)):
            if isinstance(source, str) and (source.startswith("<") or "\n" in source):
                raw_text = source
            elif isinstance(source, str) and Path(source).is_file():
                with open(source, "r", encoding="utf-8") as f:
                    raw_text = f.read()
            elif isinstance(source, bytes):
                raw_text = source.decode("utf-8")
            else:
                raw_text = str(source)
        elif hasattr(source, "read"):
            content = source.read()
            raw_text = content.decode("utf-8") if isinstance(content, bytes) else str(content)
        else:
            raise SpbXmlValidationError(f"Fonte XML não suportada: {type(source).__name__}")

        try:
            root = ET.fromstring(raw_text)
        except ET.ParseError as exc:
            raise SpbXmlValidationError(f"Falha de sintaxe XML: {exc}") from exc

        if _strip_ns(root.tag) != "DOC":
            raise SpbXmlValidationError(
                f"Elemento raiz inválido: esperado <DOC>, obtido <{_strip_ns(root.tag)}>"
            )

        return root

    def parse_header(self, root: ET.Element) -> SpbMessageHeader:
        """Extrai o bloco <BCMSG> da raiz da mensagem SPB."""
        bcmsg = _find_child_elem(root, "BCMSG")
        if bcmsg is None:
            raise SpbXmlValidationError("Segmento de controle obrigatório <BCMSG> ausente no documento.")

        emissor = _get_required_text(bcmsg, "IdentdEmissor", "BCMSG")
        destinatario = _get_required_text(bcmsg, "IdentdDestinatario", "BCMSG")
        dom_sist = _get_required_text(bcmsg, "DomSist", "BCMSG")
        nu_op = _get_required_text(bcmsg, "NUOp", "BCMSG")
        identd_contg = _get_optional_text(bcmsg, "IdentdContg") or "N"
        identd_operad = _get_optional_text(bcmsg, "IdentdOperad")

        # Grupo_Seq
        num_seq = 1
        indr_cont = "N"
        grp_seq = _find_child_elem(bcmsg, "Grupo_Seq")
        if grp_seq is not None:
            seq_text = _get_optional_text(grp_seq, "NumSeq")
            if seq_text and seq_text.isdigit():
                num_seq = int(seq_text)
            indr_cont = _get_optional_text(grp_seq, "IndrCont") or "N"

        return SpbMessageHeader(
            emissor_ispb=emissor,
            destinatario_ispb=destinatario,
            dominio_sistema=dom_sist,
            nu_op=nu_op,
            identd_contg=identd_contg,
            identd_operad=identd_operad,
            num_seq=num_seq,
            indr_cont=indr_cont,
        )

    def parse_ldl0001(self, source: Union[str, bytes, Path, io.IOBase]) -> SpbSettlementMessage:
        """Processa e valida uma mensagem canônica LDL0001 (Resultado Líquido Multilateral)."""
        root = self.parse_spb_xml(source)
        header = self.parse_header(root)

        sismsg = _find_child_elem(root, "SISMSG")
        if sismsg is None:
            raise SpbXmlValidationError("Segmento funcional obrigatório <SISMSG> ausente.")

        ldl_elem = _find_child_elem(sismsg, "LDL0001")
        if ldl_elem is None:
            raise SpbXmlValidationError("Mensagem <LDL0001> não encontrada dentro de <SISMSG>.")

        cod_msg = _get_required_text(ldl_elem, "CodMsg", "LDL0001")
        if cod_msg != "LDL0001":
            raise SpbXmlValidationError(f"Tag <CodMsg> deve ser 'LDL0001', obtido {cod_msg!r}.")

        num_ctrl_ldl = _get_required_text(ldl_elem, "NumCtrlLDL", "LDL0001")
        num_ctrl_str = _get_required_text(ldl_elem, "NumCtrlSTR", "LDL0001")

        # Câmaras podem informar <ISPBCamara> ou <ISPBPrincipal>
        ispb_camara_raw = _get_optional_text(ldl_elem, "ISPBCamara") or _get_optional_text(ldl_elem, "ISPBPrincipal") or header.emissor_ispb
        ispb_camara = validate_ispb(ispb_camara_raw, "ISPBCamara")

        ispb_if_raw = _get_required_text(ldl_elem, "ISPBIF", "LDL0001")
        ispb_participante = validate_ispb(ispb_if_raw, "ISPBIF")

        tp_deb_cred = _get_required_text(ldl_elem, "TpDeb_Cred", "LDL0001").upper()
        if tp_deb_cred not in ("C", "D"):
            raise SpbXmlValidationError(f"Campo <TpDeb_Cred> deve ser 'C' ou 'D', obtido {tp_deb_cred!r}.")

        vlr_str = _get_required_text(ldl_elem, "VlrLiqd", "LDL0001")
        valor_liq = _parse_xml_decimal(vlr_str, "VlrLiqd")

        dt_str = _get_required_text(ldl_elem, "DtLiqd", "LDL0001")
        data_liq = _parse_xml_date(dt_str, "DtLiqd")

        tp_ct = _get_optional_text(ldl_elem, "TpCt") or "CC"
        num_ct = _get_required_text(ldl_elem, "NumCt", "LDL0001")
        ag_lanc = _get_optional_text(ldl_elem, "AgLanc")
        cod_camara = _get_optional_text(ldl_elem, "CodCamara") or "B3"

        record = LdlSettlementRecord(
            cod_msg=cod_msg,
            num_ctrl_ldl=num_ctrl_ldl,
            num_ctrl_str=num_ctrl_str,
            ispb_camara=ispb_camara,
            ispb_participante=ispb_participante,
            tipo_debito_credito=tp_deb_cred,
            valor_liquidacao=valor_liq,
            data_liquidacao=data_liq,
            tipo_conta=tp_ct,
            numero_conta=num_ct,
            agencia=ag_lanc,
            codigo_camara=cod_camara,
        )

        raw_xml = ET.tostring(root, encoding="unicode")
        return SpbSettlementMessage(header=header, record=record, raw_xml=raw_xml)

    def parse_str0010(self, source: Union[str, bytes, Path, io.IOBase]) -> SpbSettlementMessage:
        """Processa e valida uma mensagem STR0010 (Transferência de Cobertura de Débitos)."""
        root = self.parse_spb_xml(source)
        header = self.parse_header(root)

        sismsg = _find_child_elem(root, "SISMSG")
        if sismsg is None:
            raise SpbXmlValidationError("Segmento funcional obrigatório <SISMSG> ausente.")

        str_elem = _find_child_elem(sismsg, "STR0010")
        if str_elem is None:
            raise SpbXmlValidationError("Mensagem <STR0010> não encontrada dentro de <SISMSG>.")

        cod_msg = _get_required_text(str_elem, "CodMsg", "STR0010")
        if cod_msg != "STR0010":
            raise SpbXmlValidationError(f"Tag <CodMsg> deve ser 'STR0010', obtido {cod_msg!r}.")

        num_ctrl_if = _get_required_text(str_elem, "NumCtrlIF", "STR0010")
        num_ctrl_str = _get_optional_text(str_elem, "NumCtrlSTR")

        ispb_debtd = validate_ispb(_get_required_text(str_elem, "ISPBIFDebtd", "STR0010"), "ISPBIFDebtd")
        ispb_credtd = validate_ispb(_get_required_text(str_elem, "ISPBIFCredtd", "STR0010"), "ISPBIFCredtd")

        vlr_str = _get_required_text(str_elem, "VlrLanc", "STR0010")
        valor = _parse_xml_decimal(vlr_str, "VlrLanc")

        dt_str = _get_required_text(str_elem, "DtAgendt", "STR0010")
        data_agend = _parse_xml_date(dt_str, "DtAgendt")

        tp_ct_deb = _get_optional_text(str_elem, "TpCtDebtd") or "RESERVAS"
        num_ct_deb = _get_required_text(str_elem, "NumCtDebtd", "STR0010")

        tp_ct_cred = _get_optional_text(str_elem, "TpCtCredtd") or "LIQUIDACAO"
        num_ct_cred = _get_required_text(str_elem, "NumCtCredtd", "STR0010")

        finalidade = _get_optional_text(str_elem, "Finald") or "LIQUIDACAO MULTILATERAL B3 CLEARING"

        record = StrTransferRecord(
            cod_msg=cod_msg,
            num_ctrl_if=num_ctrl_if,
            num_ctrl_str=num_ctrl_str,
            ispb_if_debitada=ispb_debtd,
            ispb_if_creditada=ispb_credtd,
            valor_transferencia=valor,
            data_agendamento=data_agend,
            tipo_conta_debitada=tp_ct_deb,
            numero_conta_debitada=num_ct_deb,
            tipo_conta_creditada=tp_ct_cred,
            numero_conta_creditada=num_ct_cred,
            finalidade=finalidade,
        )

        raw_xml = ET.tostring(root, encoding="unicode")
        return SpbSettlementMessage(header=header, record=record, raw_xml=raw_xml)

    # -----------------------------------------------------------------------
    # Geração de Mensagens XML
    # -----------------------------------------------------------------------

    def generate_ldl0001(
        self,
        header: SpbMessageHeader,
        record: LdlSettlementRecord,
        pretty: bool = True,
    ) -> str:
        """Gera XML canônico e estrito para a mensagem LDL0001 aderente ao XSD do BACEN."""
        root = ET.Element("DOC", attrib={"xmlns": XML_NS_LDL0001})

        # BCMSG
        bcmsg = ET.SubElement(root, "BCMSG")
        ET.SubElement(bcmsg, "IdentdEmissor").text = header.emissor_ispb
        ET.SubElement(bcmsg, "IdentdDestinatario").text = header.destinatario_ispb
        ET.SubElement(bcmsg, "IdentdContg").text = header.identd_contg
        if header.identd_operad:
            ET.SubElement(bcmsg, "IdentdOperad").text = header.identd_operad
        grp_seq = ET.SubElement(bcmsg, "Grupo_Seq")
        ET.SubElement(grp_seq, "NumSeq").text = str(header.num_seq)
        ET.SubElement(grp_seq, "IndrCont").text = header.indr_cont
        ET.SubElement(bcmsg, "DomSist").text = header.dominio_sistema
        ET.SubElement(bcmsg, "NUOp").text = header.nu_op

        # SISMSG
        sismsg = ET.SubElement(root, "SISMSG")
        ldl = ET.SubElement(sismsg, "LDL0001")
        ET.SubElement(ldl, "CodMsg").text = "LDL0001"
        ET.SubElement(ldl, "NumCtrlLDL").text = record.num_ctrl_ldl
        ET.SubElement(ldl, "NumCtrlSTR").text = record.num_ctrl_str
        ET.SubElement(ldl, "ISPBCamara").text = record.ispb_camara
        ET.SubElement(ldl, "ISPBIF").text = record.ispb_participante
        ET.SubElement(ldl, "TpDeb_Cred").text = record.tipo_debito_credito
        ET.SubElement(ldl, "VlrLiqd").text = f"{record.valor_liquidacao:.2f}"
        ET.SubElement(ldl, "DtLiqd").text = record.data_liquidacao.isoformat()
        ET.SubElement(ldl, "TpCt").text = record.tipo_conta
        ET.SubElement(ldl, "NumCt").text = record.numero_conta
        if record.agencia:
            ET.SubElement(ldl, "AgLanc").text = record.agencia
        ET.SubElement(ldl, "CodCamara").text = record.codigo_camara

        if pretty:
            self._indent_xml(root)

        xml_str = ET.tostring(root, encoding="utf-8").decode("utf-8")
        return f'<?xml version="1.0" encoding="UTF-8"?>\n{xml_str}\n'

    def generate_str0010(
        self,
        header: SpbMessageHeader,
        record: StrTransferRecord,
        pretty: bool = True,
    ) -> str:
        """Gera XML canônico e estrito para a mensagem STR0010 aderente ao XSD do BACEN."""
        root = ET.Element("DOC", attrib={"xmlns": XML_NS_STR0010})

        # BCMSG
        bcmsg = ET.SubElement(root, "BCMSG")
        ET.SubElement(bcmsg, "IdentdEmissor").text = header.emissor_ispb
        ET.SubElement(bcmsg, "IdentdDestinatario").text = header.destinatario_ispb
        ET.SubElement(bcmsg, "IdentdContg").text = header.identd_contg
        if header.identd_operad:
            ET.SubElement(bcmsg, "IdentdOperad").text = header.identd_operad
        grp_seq = ET.SubElement(bcmsg, "Grupo_Seq")
        ET.SubElement(grp_seq, "NumSeq").text = str(header.num_seq)
        ET.SubElement(grp_seq, "IndrCont").text = header.indr_cont
        ET.SubElement(bcmsg, "DomSist").text = header.dominio_sistema
        ET.SubElement(bcmsg, "NUOp").text = header.nu_op

        # SISMSG
        sismsg = ET.SubElement(root, "SISMSG")
        strmsg = ET.SubElement(sismsg, "STR0010")
        ET.SubElement(strmsg, "CodMsg").text = "STR0010"
        ET.SubElement(strmsg, "NumCtrlIF").text = record.num_ctrl_if
        if record.num_ctrl_str:
            ET.SubElement(strmsg, "NumCtrlSTR").text = record.num_ctrl_str
        ET.SubElement(strmsg, "ISPBIFDebtd").text = record.ispb_if_debitada
        ET.SubElement(strmsg, "ISPBIFCredtd").text = record.ispb_if_creditada
        ET.SubElement(strmsg, "VlrLanc").text = f"{record.valor_transferencia:.2f}"
        ET.SubElement(strmsg, "DtAgendt").text = record.data_agendamento.isoformat()
        ET.SubElement(strmsg, "TpCtDebtd").text = record.tipo_conta_debitada
        ET.SubElement(strmsg, "NumCtDebtd").text = record.numero_conta_debitada
        ET.SubElement(strmsg, "TpCtCredtd").text = record.tipo_conta_creditada
        ET.SubElement(strmsg, "NumCtCredtd").text = record.numero_conta_creditada
        ET.SubElement(strmsg, "Finald").text = record.finalidade

        if pretty:
            self._indent_xml(root)

        xml_str = ET.tostring(root, encoding="utf-8").decode("utf-8")
        return f'<?xml version="1.0" encoding="UTF-8"?>\n{xml_str}\n'

    @staticmethod
    def _indent_xml(elem: ET.Element, level: int = 0) -> None:
        """Formata identação de elementos XML in-place."""
        i = "\n" + level * "  "
        if len(elem):
            if not elem.text or not elem.text.strip():
                elem.text = i + "  "
            if not elem.tail or not elem.tail.strip():
                elem.tail = i
            for child in elem:
                SpbSettlementEngine._indent_xml(child, level + 1)
            if not child.tail or not child.tail.strip():
                child.tail = i
        else:
            if level and (not elem.tail or not elem.tail.strip()):
                elem.tail = i

    # -----------------------------------------------------------------------
    # Conciliação Cruzada Determinística com IMBARQ004
    # -----------------------------------------------------------------------

    def reconcile_with_imbarq004(
        self,
        ldl_input: Union[SpbSettlementMessage, LdlSettlementRecord, Path, str],
        imbarq_input: Any,  # Imbarq004File, Path ou str
        target_date: Optional[date] = None,
        scope: str = "auto",
        tolerance: Decimal = Decimal("0.00"),
    ) -> SettlementReconciliationResult:
        """Realiza batimento cruzado formal entre o saldo apurado no IMBARQ004 e a notificação LDL0001.

        Args:
            ldl_input: Registro LdlSettlementRecord, SpbSettlementMessage ou caminho/conteúdo XML.
            imbarq_input: Objeto Imbarq004File ou caminho/linhas do arquivo IMBARQ004.
            target_date: Data opcional para filtrar lançamentos específicos no IMBARQ004.
            scope: Escopo da conciliação ('auto', 'trailer', 'settlement_date').
            tolerance: Tolerância monetária permitida (padrão Decimal('0.00') para batimento exato).

        Returns:
            SettlementReconciliationResult com os detalhes e status da conciliação.
        """
        # 1. Resolução do registro LDL0001
        if isinstance(ldl_input, SpbSettlementMessage):
            if not isinstance(ldl_input.record, LdlSettlementRecord):
                raise SpbError("A mensagem SPB informada não contém um registro LDL0001.")
            ldl = ldl_input.record
        elif isinstance(ldl_input, LdlSettlementRecord):
            ldl = ldl_input
        else:
            parsed_msg = self.parse_ldl0001(ldl_input)
            assert isinstance(parsed_msg.record, LdlSettlementRecord)
            ldl = parsed_msg.record

        # 2. Resolução do arquivo IMBARQ004
        imbarq_file = imbarq_input
        if not hasattr(imbarq_file, "trailer") or not hasattr(imbarq_file, "detalhes"):
            from src.imbarq004 import parse_imbarq004
            imbarq_file = parse_imbarq004(imbarq_input)

        # 3. Determinação do saldo esperado a partir do IMBARQ004
        expected_valor: Decimal
        expected_natureza: str
        detalhes: List[str] = []

        eff_date = target_date or ldl.data_liquidacao

        if scope == "trailer":
            expected_valor = imbarq_file.trailer.total_saldo_liquido
            expected_natureza = imbarq_file.trailer.sinal_saldo_liquido
        elif scope == "settlement_date":
            postings = [d for d in imbarq_file.detalhes if d.data_liquidacao == eff_date]
            if not postings:
                raise SpbReconciliationMismatchError(
                    f"Nenhum lançamento no IMBARQ004 para a data de liquidação {eff_date}."
                )
            c_val = sum((p.valor_lancamento for p in postings if p.natureza_lancamento == "C"), Decimal("0.00"))
            d_val = sum((p.valor_lancamento for p in postings if p.natureza_lancamento == "D"), Decimal("0.00"))
            net = c_val - d_val
            expected_valor = abs(net)
            expected_natureza = "C" if net >= Decimal("0.00") else "D"
        else:  # 'auto'
            # Tenta verificar se o trailer total coincide com a LDL
            if (
                imbarq_file.trailer.total_saldo_liquido == ldl.valor_liquidacao
                and imbarq_file.trailer.sinal_saldo_liquido == ldl.tipo_debito_credito
            ):
                expected_valor = imbarq_file.trailer.total_saldo_liquido
                expected_natureza = imbarq_file.trailer.sinal_saldo_liquido
            else:
                # Tenta verificar os lançamentos específicos da data de liquidação da LDL
                postings = [d for d in imbarq_file.detalhes if d.data_liquidacao == eff_date]
                if postings:
                    c_val = sum((p.valor_lancamento for p in postings if p.natureza_lancamento == "C"), Decimal("0.00"))
                    d_val = sum((p.valor_lancamento for p in postings if p.natureza_lancamento == "D"), Decimal("0.00"))
                    net = c_val - d_val
                    expected_valor = abs(net)
                    expected_natureza = "C" if net >= Decimal("0.00") else "D"
                else:
                    # Se não houver filtro ou não bater por data, utiliza o trailer
                    expected_valor = imbarq_file.trailer.total_saldo_liquido
                    expected_natureza = imbarq_file.trailer.sinal_saldo_liquido

        # 4. Avaliação de Discrepâncias
        diff = abs(ldl.valor_liquidacao - expected_valor)
        status = "MATCHED"

        if diff > tolerance:
            status = "MISMATCH_AMOUNT"
            detalhes.append(
                f"Divergência de montante: IMBARQ004 apurou R$ {expected_valor:.2f}, "
                f"mas SPB LDL0001 reportou R$ {ldl.valor_liquidacao:.2f} (diferença: R$ {diff:.2f})."
            )

        if ldl.tipo_debito_credito != expected_natureza:
            if status == "MATCHED":
                status = "MISMATCH_DIRECTION"
            detalhes.append(
                f"Divergência de natureza financeira: IMBARQ004 indica '{expected_natureza}', "
                f"mas SPB LDL0001 informou '{ldl.tipo_debito_credito}'."
            )

        if target_date and ldl.data_liquidacao != target_date:
            if status == "MATCHED":
                status = "MISMATCH_DATE"
            detalhes.append(
                f"Divergência de data de liquidação: esperado {target_date}, "
                f"mas SPB LDL0001 veio com {ldl.data_liquidacao}."
            )

        is_reconciled = (len(detalhes) == 0)
        if is_reconciled:
            detalhes.append(
                f"Batimento determinístico aprovado: R$ {ldl.valor_liquidacao:.2f} {ldl.tipo_debito_credito} "
                f"coincide integralmente entre IMBARQ004 e LDL0001 (NumCtrlSTR: {ldl.num_ctrl_str})."
            )

        return SettlementReconciliationResult(
            is_reconciled=is_reconciled,
            status=status,
            imbarq004_saldo_liquido=expected_valor,
            imbarq004_natureza=expected_natureza,
            spb_valor=ldl.valor_liquidacao,
            spb_natureza=ldl.tipo_debito_credito,
            diff_valor=diff,
            detalhes=detalhes,
        )
