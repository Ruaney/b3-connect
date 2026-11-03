"""Cliente para automação de download de relatórios via API iMercado Conciliação (FL-001).

Este módulo implementa a comunicação com a API iMercado da B3 para:
1. Autenticação via protocolo OAuth 2.0 (client_credentials) com suporte a mTLS;
2. Solicitação de URLs temporárias pré-assinadas (Shared Access Signature - SAS);
3. Download resiliente com retries exponenciais e validação estrita de integridade
   (Content-Length e SHA-256).

Totalmente desacoplado de dependências externas pesadas (utiliza stdlib Python).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
import hashlib
import json
import logging
from pathlib import Path
import ssl
import time
from typing import Any, Dict, List, Optional, Union
import urllib.error
import urllib.parse
import urllib.request


# ---------------------------------------------------------------------------
# Exceções Especializadas
# ---------------------------------------------------------------------------

class IMercadoError(Exception):
    """Exceção base para erros do cliente de integração iMercado."""
    pass


class AuthenticationError(IMercadoError):
    """Lançada quando a autenticação OAuth 2.0 falha."""
    pass


class TokenExpiredError(AuthenticationError):
    """Lançada quando o token de acesso Bearer expirou."""
    pass


class SasUrlRequestError(IMercadoError):
    """Lançada quando a solicitação de SAS URL é rejeitada ou retorna erro."""
    pass


class DownloadError(IMercadoError):
    """Lançada quando ocorre erro durante o download ou tentativas são esgotadas."""
    pass


class IntegrityError(DownloadError):
    """Exceção base para falhas de validação de integridade do arquivo."""
    pass


class ContentLengthMismatchError(IntegrityError):
    """Lançada quando o tamanho recebido não coincide com o cabeçalho Content-Length."""

    def __init__(self, expected_bytes: int, actual_bytes: int) -> None:
        self.expected_bytes = expected_bytes
        self.actual_bytes = actual_bytes
        super().__init__(
            f"Divergência de Content-Length: esperado {expected_bytes} bytes, "
            f"recebido {actual_bytes} bytes."
        )


class HashMismatchError(IntegrityError):
    """Lançada quando o hash SHA-256 calculado diverge do hash esperado."""

    def __init__(self, expected_hash: str, actual_hash: str) -> None:
        self.expected_hash = expected_hash
        self.actual_hash = actual_hash
        super().__init__(
            f"Divergência de integridade SHA-256:\n"
            f" - Esperado : {expected_hash}\n"
            f" - Calculado: {actual_hash}"
        )


# ---------------------------------------------------------------------------
# Modelos de Dados Tipados (Dataclasses)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AuthToken:
    """Representação de token Bearer OAuth 2.0 com controle de TTL."""
    access_token: str
    token_type: str = "Bearer"
    expires_in: int = 3600
    issued_at: float = field(default_factory=time.time)

    def is_expired(self, buffer_seconds: int = 60) -> bool:
        """Verifica se o token expirou considerando uma margem de segurança (buffer)."""
        return time.time() >= (self.issued_at + self.expires_in - buffer_seconds)

    @property
    def authorization_header(self) -> str:
        """Retorna o valor formatado para o cabeçalho HTTP Authorization."""
        return f"{self.token_type} {self.access_token}"


@dataclass(frozen=True)
class SasUrlResponse:
    """Metadados e URL SAS retornados pela API iMercado Conciliação."""
    sas_url: str
    report_type: str
    movement_date: str
    participant_code: str
    file_name: str
    file_size: Optional[int] = None
    sha256_hash: Optional[str] = None
    expires_at: Optional[str] = None
    raw_response: Dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class DownloadResult:
    """Resultado do download com métricas de integridade e auditoria de transporte."""
    destination_path: Path
    bytes_downloaded: int
    sha256_hash: str
    attempts: int
    elapsed_seconds: float


# ---------------------------------------------------------------------------
# Cliente de Integração iMercado
# ---------------------------------------------------------------------------

class IMercadoClient:
    """Cliente desacoplado para download automatizado de arquivos da API iMercado B3.

    Implementa a jornada FL-001:
    - Autenticação OAuth 2.0 com suporte a certificados mTLS da B3;
    - Requisição de SAS URLs temporárias;
    - Download resiliente com retries exponenciais e verificação de SHA-256;
    - Modo mock integrado para execução desacoplada em testes locais e pipelines.
    """

    def __init__(
        self,
        base_url: str = "https://api.imercado.b3.com.br",
        auth_url: Optional[str] = None,
        timeout_seconds: float = 30.0,
        mock_mode: bool = False,
        fixtures_dir: Optional[Union[str, Path]] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.auth_url = auth_url.rstrip("/") if auth_url else f"{self.base_url}/oauth/token"
        self.timeout_seconds = timeout_seconds
        self.mock_mode = mock_mode
        self.fixtures_dir = (
            Path(fixtures_dir)
            if fixtures_dir
            else Path(__file__).resolve().parent.parent / "fixtures"
        )
        self.logger = logger or logging.getLogger("imercado_client")
        self._current_token: Optional[AuthToken] = None

        # Repositórios internos para simulação de dados e falhas em modo mock
        self._mock_files: Dict[str, bytes] = {}
        self._mock_failures: Dict[str, List[Exception]] = {}

    @property
    def current_token(self) -> Optional[AuthToken]:
        """Retorna o token ativo armazenado em cache no cliente, se houver."""
        return self._current_token

    def register_mock_file(self, sas_url: str, data: bytes) -> None:
        """Registra conteúdo sintético vinculado a uma SAS URL para simulação de download."""
        self._mock_files[sas_url] = data

    def register_mock_failures(self, sas_url: str, errors: List[Exception]) -> None:
        """Registra sequência de exceções para simular falhas transitórias de transporte."""
        self._mock_failures[sas_url] = list(errors)

    def clear_mock_registries(self) -> None:
        """Limpa registros de mocks e falhas simuladas."""
        self._mock_files.clear()
        self._mock_failures.clear()

    # -----------------------------------------------------------------------
    # 1. Autenticação OAuth 2.0 (client_credentials + mTLS)
    # -----------------------------------------------------------------------

    def authenticate(
        self,
        client_id: str,
        client_secret: str,
        cert_path: Optional[str] = None,
    ) -> AuthToken:
        """Executa ou simula autenticação OAuth 2.0 obtendo token Bearer com TTL.

        Args:
            client_id: Identificador institucional do participante na B3.
            client_secret: Segredo corporativo de autenticação.
            cert_path: Caminho opcional para o certificado digital ICP-Brasil (mTLS).

        Returns:
            AuthToken: Objeto com token de acesso, tipo e validade.

        Raises:
            AuthenticationError: Se as credenciais forem inválidas ou houver erro na conexão.
        """
        if not client_id or not client_id.strip():
            raise AuthenticationError("Parâmetro 'client_id' é obrigatório e não pode ser vazio.")
        if not client_secret or not client_secret.strip():
            raise AuthenticationError("Parâmetro 'client_secret' é obrigatório e não pode ser vazio.")

        if self.mock_mode:
            self.logger.info("Modo mock ativado: simulando emissão de token OAuth 2.0 para %s", client_id)
            if client_id == "invalid" or client_secret == "invalid":
                raise AuthenticationError("Credenciais inválidas informadas no modo mock.")

            token_hash = hashlib.sha256(f"{client_id}:{time.time()}".encode("utf-8")).hexdigest()[:32]
            token = AuthToken(
                access_token=f"mock_token_{token_hash}",
                token_type="Bearer",
                expires_in=3600,
                issued_at=time.time(),
            )
            self._current_token = token
            return token

        # Execução real via HTTP POST
        post_data = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        }).encode("utf-8")

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": "B3-Integration-Atlas/0.1.0",
        }
        req = urllib.request.Request(self.auth_url, data=post_data, headers=headers)

        ssl_context: Optional[ssl.SSLContext] = None
        if cert_path:
            ssl_context = ssl.create_default_context()
            ssl_context.load_cert_chain(certfile=cert_path)

        try:
            with urllib.request.urlopen(req, context=ssl_context, timeout=self.timeout_seconds) as resp:
                status = getattr(resp, "status", 200)
                if status != 200:
                    raise AuthenticationError(f"Falha de autenticação OAuth: status HTTP {status}")
                payload = json.loads(resp.read().decode("utf-8"))

            token = AuthToken(
                access_token=payload["access_token"],
                token_type=payload.get("token_type", "Bearer"),
                expires_in=int(payload.get("expires_in", 3600)),
                issued_at=time.time(),
            )
            self._current_token = token
            return token

        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            raise AuthenticationError(f"Erro HTTP {e.code} ao autenticar OAuth: {err_body or e.reason}") from e
        except urllib.error.URLError as e:
            raise AuthenticationError(f"Falha de conexão com endpoint de autenticação: {e.reason}") from e
        except json.JSONDecodeError as e:
            raise AuthenticationError(f"Resposta de autenticação não é um JSON válido: {e}") from e

    # -----------------------------------------------------------------------
    # 2. Solicitação de SAS URL
    # -----------------------------------------------------------------------

    def request_sas_url(
        self,
        token: Optional[Union[str, AuthToken]],
        report_type: str,
        movement_date: Union[str, date],
        participant_code: Union[str, int],
    ) -> SasUrlResponse:
        """Solicita a URL SAS temporária de download do relatório B3.

        Args:
            token: Token Bearer em string ou objeto AuthToken emitido por authenticate().
            report_type: Identificador do relatório (ex: 'IMBARQ006', 'IMBARQ004').
            movement_date: Data de movimento do relatório (date ou 'YYYY-MM-DD' / 'YYYYMMDD').
            participant_code: Código do participante cadastrado na B3.

        Returns:
            SasUrlResponse: URL assinada temporária e metadados do arquivo.

        Raises:
            AuthenticationError: Se o token for ausente ou inválido.
            TokenExpiredError: Se o token estiver expirado.
            SasUrlRequestError: Se a API iMercado rejeitar a solicitação.
        """
        # Resolução e validação de token
        if token is None:
            if self._current_token is not None:
                token = self._current_token
            else:
                raise AuthenticationError("Token de acesso não fornecido e cliente não autenticado.")

        if isinstance(token, AuthToken):
            if token.is_expired():
                raise TokenExpiredError("Token de acesso expirado. Renove via authenticate().")
            token_str = token.access_token
        elif isinstance(token, str):
            token_str = token.strip()
            if not token_str:
                raise AuthenticationError("Token de acesso não pode ser vazio.")
        else:
            raise AuthenticationError(f"Tipo de token inválido: {type(token)}")

        if not report_type or not report_type.strip():
            raise SasUrlRequestError("Parâmetro 'report_type' não pode ser vazio.")

        rep_clean = report_type.strip().upper()
        part_clean = str(participant_code).strip()
        if not part_clean:
            raise SasUrlRequestError("Parâmetro 'participant_code' não pode ser vazio.")

        # Normalização de data
        if isinstance(movement_date, date):
            date_iso = movement_date.strftime("%Y-%m-%d")
            date_compact = movement_date.strftime("%Y%m%d")
        elif isinstance(movement_date, str):
            clean_str = movement_date.strip().replace("-", "")
            if len(clean_str) == 8 and clean_str.isdigit():
                date_iso = f"{clean_str[:4]}-{clean_str[4:6]}-{clean_str[6:]}"
                date_compact = clean_str
            else:
                date_iso = movement_date.strip()
                date_compact = clean_str
        else:
            raise SasUrlRequestError(f"Formato de movement_date inválido: {movement_date}")

        if self.mock_mode:
            self.logger.info(
                "Modo mock ativado: gerando SAS URL sintética para %s / part %s / data %s",
                rep_clean, part_clean, date_iso
            )
            file_name = f"{rep_clean}_{part_clean}_{date_compact}.txt"
            mock_url = (
                f"mock://blob.core.windows.net/imercado-conciliacao/{file_name}"
                f"?sig=mock_sas_sig_{part_clean}&se=2026-11-04T00:00:00Z"
            )

            # Verifica se já há dados registrados ou carrega de fixture correspondente
            if mock_url in self._mock_files:
                content = self._mock_files[mock_url]
            else:
                fixture_file = self.fixtures_dir / f"{rep_clean.lower()}-valid.txt"
                if not fixture_file.exists():
                    fixture_file = self.fixtures_dir / "imbarq006-valid.txt"

                if fixture_file.exists():
                    content = fixture_file.read_bytes()
                else:
                    content = f"HEADER {rep_clean} {part_clean} {date_iso}\nTRAILER 00000001\n".encode("utf-8")
                self._mock_files[mock_url] = content

            file_size = len(content)
            sha256_hash = hashlib.sha256(content).hexdigest()

            return SasUrlResponse(
                sas_url=mock_url,
                report_type=rep_clean,
                movement_date=date_iso,
                participant_code=part_clean,
                file_name=file_name,
                file_size=file_size,
                sha256_hash=sha256_hash,
                expires_at="2026-11-04T00:00:00Z",
                raw_response={
                    "sas_url": mock_url,
                    "file_name": file_name,
                    "file_size": file_size,
                    "sha256_hash": sha256_hash,
                    "mock": True,
                },
            )

        # Execução real via HTTP GET
        query_params = urllib.parse.urlencode({
            "data_movimento": date_iso,
            "participante": part_clean,
        })
        endpoint_url = f"{self.base_url}/v1/conciliacao/relatorios/{rep_clean}/sas-url?{query_params}"

        headers = {
            "Authorization": f"Bearer {token_str}",
            "Accept": "application/json",
            "User-Agent": "B3-Integration-Atlas/0.1.0",
        }
        req = urllib.request.Request(endpoint_url, headers=headers)

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                status = getattr(resp, "status", 200)
                if status != 200:
                    raise SasUrlRequestError(f"Falha ao solicitar SAS URL: status HTTP {status}")
                payload = json.loads(resp.read().decode("utf-8"))

            sas_url = payload.get("sas_url") or payload.get("sasUrl") or payload.get("url")
            if not sas_url:
                raise SasUrlRequestError(f"Resposta da API iMercado não continha o campo 'sas_url': {payload}")

            file_name = (
                payload.get("file_name")
                or payload.get("fileName")
                or payload.get("nomeArquivo")
                or f"{rep_clean}_{part_clean}_{date_compact}.txt"
            )
            raw_size = payload.get("file_size") or payload.get("fileSize") or payload.get("tamanhoArquivo")
            file_size = int(raw_size) if raw_size is not None else None
            sha256_hash = (
                payload.get("sha256_hash")
                or payload.get("sha256Hash")
                or payload.get("hashSha256")
                or payload.get("hash")
            )
            expires_at = payload.get("expires_at") or payload.get("expiresAt") or payload.get("expiracao")

            return SasUrlResponse(
                sas_url=sas_url,
                report_type=rep_clean,
                movement_date=date_iso,
                participant_code=part_clean,
                file_name=file_name,
                file_size=file_size,
                sha256_hash=sha256_hash,
                expires_at=expires_at,
                raw_response=payload,
            )

        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            raise SasUrlRequestError(f"Erro HTTP {e.code} ao obter SAS URL: {err_body or e.reason}") from e
        except urllib.error.URLError as e:
            raise SasUrlRequestError(f"Falha de conexão ao obter SAS URL: {e.reason}") from e
        except json.JSONDecodeError as e:
            raise SasUrlRequestError(f"Resposta da API iMercado com JSON inválido: {e}") from e

    # -----------------------------------------------------------------------
    # 3. Download Resiliente com Retries e Verificação de Hash
    # -----------------------------------------------------------------------

    def download_report(
        self,
        sas_url: str,
        destination_path: Union[str, Path],
        expected_hash_sha256: Optional[str] = None,
        max_retries: int = 3,
        retry_delay: float = 0.5,
        backoff_factor: float = 2.0,
    ) -> DownloadResult:
        """Realiza o download de relatório com suporte a retries e validação de integridade.

        Args:
            sas_url: URL assinada (SAS) para download via Azure Blob Storage ou simulação mock.
            destination_path: Caminho de destino para gravação do arquivo baixado.
            expected_hash_sha256: Hash SHA-256 esperado retornado pela API ou nulo.
            max_retries: Número máximo de tentativas de download para falhas transitórias.
            retry_delay: Tempo inicial de espera (segundos) entre retries.
            backoff_factor: Fator de multiplicação exponencial para retries subsequentes.

        Returns:
            DownloadResult: Informações do download, total de bytes, hash e tentativas.

        Raises:
            HashMismatchError: Se o hash calculado divergir do esperado.
            ContentLengthMismatchError: Se o total de bytes divergir de Content-Length.
            DownloadError: Se todas as tentativas falharem ou ocorrer erro irrecuperável.
        """
        if not sas_url or not sas_url.strip():
            raise DownloadError("sas_url não pode ser vazia.")

        destination = Path(destination_path).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = destination.with_suffix(destination.suffix + f".tmp_{time.time_ns()}")

        attempt = 0
        start_time = time.time()

        while attempt < max_retries:
            attempt += 1
            try:
                # 1. Simulação de falhas transitórias registradas para teste
                if sas_url in self._mock_failures and self._mock_failures[sas_url]:
                    simulated_exception = self._mock_failures[sas_url].pop(0)
                    raise simulated_exception

                # 2. Execução em modo mock ou URL simulada
                if self.mock_mode or sas_url.startswith("mock://"):
                    if sas_url in self._mock_files:
                        content_bytes = self._mock_files[sas_url]
                    else:
                        fixture_candidate = self.fixtures_dir / "imbarq006-valid.txt"
                        if fixture_candidate.exists():
                            content_bytes = fixture_candidate.read_bytes()
                        else:
                            content_bytes = b"MOCK_IMERCADO_DEFAULT_PAYLOAD\n"

                    bytes_count = len(content_bytes)
                    hasher = hashlib.sha256()
                    with open(tmp_path, "wb") as f:
                        f.write(content_bytes)
                        hasher.update(content_bytes)
                    actual_hash = hasher.hexdigest()

                # 3. Execução real via HTTP/HTTPS utilizando urllib
                else:
                    req = urllib.request.Request(
                        sas_url,
                        headers={"User-Agent": "B3-Integration-Atlas/0.1.0"},
                    )
                    with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                        status = getattr(resp, "status", 200)
                        if status not in (200, 206):
                            raise DownloadError(f"Status HTTP {status} inesperado ao baixar de {sas_url}")

                        content_len_header = resp.headers.get("Content-Length")
                        expected_bytes = (
                            int(content_len_header)
                            if content_len_header and content_len_header.isdigit()
                            else None
                        )

                        hasher = hashlib.sha256()
                        bytes_count = 0
                        with open(tmp_path, "wb") as f:
                            while True:
                                chunk = resp.read(64 * 1024)
                                if not chunk:
                                    break
                                f.write(chunk)
                                hasher.update(chunk)
                                bytes_count += len(chunk)

                        if expected_bytes is not None and bytes_count != expected_bytes:
                            raise ContentLengthMismatchError(
                                expected_bytes=expected_bytes, actual_bytes=bytes_count
                            )

                        actual_hash = hasher.hexdigest()

                # 4. Validação estrita de Hash SHA-256
                if expected_hash_sha256 is not None:
                    normalized_expected = expected_hash_sha256.strip().lower()
                    if actual_hash.lower() != normalized_expected:
                        if tmp_path.exists():
                            tmp_path.unlink(missing_ok=True)
                        raise HashMismatchError(
                            expected_hash=normalized_expected,
                            actual_hash=actual_hash,
                        )

                # 5. Conclusão atômica: substituição do arquivo temporário verificado
                tmp_path.replace(destination)
                elapsed = time.time() - start_time
                self.logger.info(
                    "Download concluído com sucesso: %s (%d bytes, hash=%s, tentativas=%d em %.2fs)",
                    destination.name, bytes_count, actual_hash[:12], attempt, elapsed
                )
                return DownloadResult(
                    destination_path=destination,
                    bytes_downloaded=bytes_count,
                    sha256_hash=actual_hash,
                    attempts=attempt,
                    elapsed_seconds=elapsed,
                )

            except HashMismatchError:
                # Divergência de integridade de hash é erro estrito e não deve ser mascarada
                if tmp_path.exists():
                    tmp_path.unlink(missing_ok=True)
                raise

            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, DownloadError) as e:
                if tmp_path.exists():
                    tmp_path.unlink(missing_ok=True)

                if attempt < max_retries:
                    delay = retry_delay * (backoff_factor ** (attempt - 1))
                    self.logger.warning(
                        "Tentativa %d/%d falhou (%s). Retentando download em %.2fs...",
                        attempt, max_retries, e, delay
                    )
                    if delay > 0:
                        time.sleep(delay)
                else:
                    self.logger.error(
                        "Todas as %d tentativas de download falharam para %s. Último erro: %s",
                        max_retries, sas_url, e
                    )
                    raise DownloadError(
                        f"Falha ao realizar download após {max_retries} tentativas: {e}"
                    ) from e
