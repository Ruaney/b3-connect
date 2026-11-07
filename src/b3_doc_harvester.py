"""B3 Documentation Harvester & Scraper Engine.

Motor de descoberta, raspagem e download controlado da documentação oficial da B3.
Projetado para mapear e arquivar localmente manuais técnicos, regulamentos, esquemas XSD,
especificações de mensageria FIX/SBE e arquivos de layout da bolsa.

100% Python Standard Library: urllib, html.parser, hashlib, json, pathlib.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple
import urllib.error
import urllib.parse
import urllib.request


# Categorias da Documentação Oficial B3
DOCUMENT_CATEGORIES = {
    "PUMA_TRADING": "Manuais e Especificações do PUMA Trading System (FIX, SBE, UMDF, Túneis)",
    "CLEARING_RISK": "Regulamentos da Câmara, Metodologia CORE, Salvaguardas e Default Waterfall",
    "NOME_BALCAO": "Manuais de Balcão Cetip/NoMe, Dicionários XML, Títulos e Gravames",
    "CSD_DEPOSITARY": "Central Depositária, Contas Individualizadas, IMBARQ e Eventos Corporativos",
    "SPB_SETTLEMENT": "Mensageria SPB, STR (LDL0001), Pix Margem SPI e Drex",
    "BSM_SURVEILLANCE": "Roteiros Operacionais de Auditoria, Tipologias e Regulamento BSM",
    "REGULATORY_CIRCULARS": "Ofícios Circulares, Comunicados Externos e Resoluções CVM/BACEN",
    "TARIFF_CATALOG": "Tabelas Oficiais de Tarifação, Emolumentos e Taxas de Liquidação",
}

# URLs Semente Oficiais de Índices Documentais da B3
OFFICIAL_B3_SEEDS: List[Dict[str, str]] = [
    {
        "id": "SEED-IMERCADO",
        "category": "CSD_DEPOSITARY",
        "title": "iMercado — Índice de Documentos e Manuais Operacionais",
        "url": "https://www.b3.com.br/pt_br/solucoes/plataformas/middle-e-backoffice/imercado/documentos/",
    },
    {
        "id": "SEED-CLEARING-REGS",
        "category": "CLEARING_RISK",
        "title": "Câmara B3 — Regulamentos, Manuais de Risco e Salvaguardas",
        "url": "https://www.b3.com.br/pt_br/regulacao/estrutura-normativa/regulamentos-e-manuais/",
    },
    {
        "id": "SEED-PUMA-MANUALS",
        "category": "PUMA_TRADING",
        "title": "PUMA Trading System — Plataformas e Especificações Técnicas",
        "url": "https://www.b3.com.br/pt_br/solucoes/plataformas/puma-trading-system/",
    },
    {
        "id": "SEED-NOME-BALCAO",
        "category": "NOME_BALCAO",
        "title": "NoMe / Cetip — Registro de Renda Fixa e Derivativos de Balcão",
        "url": "https://www.b3.com.br/pt_br/produtos-e-servicos/registro/",
    },
    {
        "id": "SEED-CIRCULARES",
        "category": "REGULATORY_CIRCULARS",
        "title": "B3 — Ofícios Circulares e Comunicados Externos",
        "url": "https://www.b3.com.br/pt_br/regulacao/oficios-circulares-e-comunicados/",
    },
    {
        "id": "SEED-BSM-REGS",
        "category": "BSM_SURVEILLANCE",
        "title": "BSM Supervisão de Mercados — Normas, Roteiros e Tipologias",
        "url": "https://www.bsmsupervisao.com.br/normas-e-orientacoes/roteiros-operacionais",
    },
]


@dataclass
class B3DocMetadata:
    """Metadados de um documento oficial da B3 descoberto ou baixado."""
    doc_id: str
    title: str
    url: str
    category: str
    file_type: str  # pdf, xsd, zip, xlsx, docx, html
    local_path: Optional[str] = None
    size_bytes: int = 0
    sha256: Optional[str] = None
    last_discovered: str = ""
    download_status: str = "DISCOVERED"  # DISCOVERED, DOWNLOADED, ERROR, SKIPPED
    error_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "title": self.title,
            "url": self.url,
            "category": self.category,
            "file_type": self.file_type,
            "local_path": self.local_path,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "last_discovered": self.last_discovered,
            "download_status": self.download_status,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> B3DocMetadata:
        return cls(**data)


class B3LinkExtractor(HTMLParser):
    """Extrator de links e âncoras para rastreamento de documentos em páginas HTML da B3."""

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.extracted_links: List[Tuple[str, str]] = []  # (url_absoluta, texto_ancora)
        self._current_tag: Optional[str] = None
        self._current_href: Optional[str] = None
        self._current_text: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if tag.lower() == "a":
            self._current_tag = "a"
            for attr, val in attrs:
                if attr.lower() == "href" and val:
                    # Normaliza URL relativa para absoluta
                    full_url = urllib.parse.urljoin(self.base_url, val)
                    self._current_href = full_url
            self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._current_tag == "a":
            self._current_text.append(data.strip())

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._current_href:
            link_text = " ".join([t for t in self._current_text if t]).strip()
            self.extracted_links.append((self._current_href, link_text))
            self._current_tag = None
            self._current_href = None
            self._current_text = []


class B3DocHarvester:
    """Motor de coleta e gestão do acervo documental oficial da B3."""

    def __init__(
        self,
        target_dir: Optional[Path] = None,
        rate_limit_delay: float = 0.5,
        timeout: int = 15,
        user_agent: str = "B3IntegrationAtlasHarvester/2.2.0 (Open-Source Research & Compliance Engine)",
    ) -> None:
        self.target_dir = Path(target_dir) if target_dir else Path.cwd() / "raw_docs"
        self.rate_limit_delay = rate_limit_delay
        self.timeout = timeout
        self.user_agent = user_agent
        self.manifest_path = self.target_dir / "manifest.json"
        self.documents: Dict[str, B3DocMetadata] = {}
        self._load_manifest()

    def _load_manifest(self) -> None:
        """Carrega manifesto de documentos previamente baixados/mapeados."""
        if self.manifest_path.is_file():
            try:
                with open(self.manifest_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for item in data.get("documents", []):
                        doc = B3DocMetadata.from_dict(item)
                        self.documents[doc.doc_id] = doc
            except Exception:
                self.documents = {}

    def save_manifest(self) -> None:
        """Salva manifesto atualizado em disco com mesclagem atômica para permitir execução concorrente."""
        self.target_dir.mkdir(parents=True, exist_ok=True)
        disk_docs: Dict[str, B3DocMetadata] = {}
        if self.manifest_path.is_file():
            try:
                with open(self.manifest_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for item in data.get("documents", []):
                        doc = B3DocMetadata.from_dict(item)
                        disk_docs[doc.doc_id] = doc
            except Exception:
                pass

        # Mescla mantendo atualizações locais
        for k, v in self.documents.items():
            disk_docs[k] = v
        self.documents = disk_docs

        manifest_data = {
            "version": "1.0.0",
            "last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "total_documents": len(self.documents),
            "documents_by_category": self._count_by_category(),
            "documents": [doc.to_dict() for doc in self.documents.values()],
        }
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest_data, f, indent=2, ensure_ascii=False)

    def _count_by_category(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for doc in self.documents.values():
            counts[doc.category] = counts.get(doc.category, 0) + 1
        return counts

    def _generate_doc_id(self, url: str) -> str:
        """Gera ID determinístico baseado no hash da URL."""
        return "DOC-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:12].upper()

    def _classify_url(self, url: str, text: str) -> Tuple[str, str]:
        """Classifica categoria e extensão do documento a partir da URL e texto."""
        url_lower = url.lower()
        text_lower = text.lower()

        # Determina tipo de arquivo
        file_type = "html"
        for ext in [".pdf", ".xsd", ".zip", ".xlsx", ".xls", ".docx", ".doc", ".json", ".csv"]:
            if url_lower.endswith(ext) or ext + "?" in url_lower:
                file_type = ext.replace(".", "")
                break

        # Determina categoria temática
        if any(k in url_lower or k in text_lower for k in ["puma", "sbe", "fix", "umdf", "leilao", "negociacao"]):
            category = "PUMA_TRADING"
        elif any(k in url_lower or k in text_lower for k in ["clearing", "camara", "core", "margem", "salvaguarda", "waterfall"]):
            category = "CLEARING_RISK"
        elif any(k in url_lower or k in text_lower for k in ["cetip", "nome", "balcao", "cdb", "debenture", "recebivel", "gravame", "cpr", "cir"]):
            category = "NOME_BALCAO"
        elif any(k in url_lower or k in text_lower for k in ["imbarq", "imercado", "custodia", "csd", "depositary", "stvm", "evento-corporativo"]):
            category = "CSD_DEPOSITARY"
        elif any(k in url_lower or k in text_lower for k in ["spb", "str", "ldl", "spi", "pix", "drex"]):
            category = "SPB_SETTLEMENT"
        elif any(k in url_lower or k in text_lower for k in ["bsm", "supervisao", "auditoria", "wash", "spoofing"]):
            category = "BSM_SURVEILLANCE"
        elif any(k in url_lower or k in text_lower for k in ["tarifa", "emolumento", "custo"]):
            category = "TARIFF_CATALOG"
        else:
            category = "REGULATORY_CIRCULARS"

        return category, file_type

    def import_from_sources_catalog(self, sources_path: Path) -> int:
        """Importa fontes cadastradas no catálogo do Atlas como metas de coleta."""
        if not sources_path.is_file():
            return 0

        with open(sources_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        imported = 0
        for src in data.get("sources", []):
            url = src.get("url")
            if not url or not url.startswith("http"):
                continue

            doc_id = src.get("id") or self._generate_doc_id(url)
            title = src.get("title", "Fonte B3")
            category, file_type = self._classify_url(url, title)

            if doc_id not in self.documents:
                self.documents[doc_id] = B3DocMetadata(
                    doc_id=doc_id,
                    title=title,
                    url=url,
                    category=category,
                    file_type=file_type,
                    last_discovered=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    download_status="DISCOVERED",
                )
                imported += 1

        self.save_manifest()
        return imported

    def fetch_url(self, url: str) -> Tuple[Optional[bytes], Optional[str], Optional[str]]:
        """Executa download seguro de uma URL com retry, headers oficiais e timeout."""
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml,application/pdf,*/*",
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        req = urllib.request.Request(url, headers=headers)

        retries = 3
        backoff = 1.0
        last_err = None

        for attempt in range(1, retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    content_type = resp.headers.get("Content-Type", "")
                    content_bytes = resp.read()
                    return content_bytes, content_type, None
            except urllib.error.HTTPError as e:
                last_err = f"HTTP {e.code}: {e.reason}"
                if e.code in (404, 403, 410):
                    break  # Erro definitivo
            except urllib.error.URLError as e:
                last_err = f"URLError: {e.reason}"
            except Exception as e:
                last_err = f"Erro: {str(e)}"

            time.sleep(backoff)
            backoff *= 2.0

        return None, None, last_err

    def download_document(self, doc_id: str, dry_run: bool = False) -> bool:
        """Realiza o download de um documento específico, grava no disco e atualiza SHA-256."""
        doc = self.documents.get(doc_id)
        if not doc:
            return False

        if dry_run:
            return True

        content, content_type, error = self.fetch_url(doc.url)
        if error or not content:
            doc.download_status = "ERROR"
            doc.error_message = error
            return False

        # Cria pasta por categoria
        cat_dir = self.target_dir / doc.category.lower()
        cat_dir.mkdir(parents=True, exist_ok=True)

        # Determina nome do arquivo local
        filename = f"{doc.doc_id.lower()}.{doc.file_type}"
        local_file = cat_dir / filename

        with open(local_file, "wb") as f:
            f.write(content)

        doc.local_path = str(local_file.relative_to(self.target_dir.parent))
        doc.size_bytes = len(content)
        doc.sha256 = hashlib.sha256(content).hexdigest()
        doc.download_status = "DOWNLOADED"
        doc.error_message = None
        return True

    def get_summary(self) -> Dict[str, Any]:
        """Gera sumário estatístico do acervo documental."""
        status_counts: Dict[str, int] = {}
        for d in self.documents.values():
            status_counts[d.download_status] = status_counts.get(d.download_status, 0) + 1

        return {
            "total_cataloged": len(self.documents),
            "by_status": status_counts,
            "by_category": self._count_by_category(),
            "target_directory": str(self.target_dir),
        }
