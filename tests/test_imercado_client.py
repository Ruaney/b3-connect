"""Testes automatizados para o cliente iMercado Conciliação (FL-001)."""

from datetime import date, datetime
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch
import urllib.error

# Garante que a raiz do projeto esteja no PYTHONPATH
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.imbarq006 import parse_imbarq006
from src.imercado_client import (
    AuthenticationError,
    AuthToken,
    ContentLengthMismatchError,
    DownloadError,
    DownloadResult,
    HashMismatchError,
    IMercadoClient,
    IMercadoError,
    IntegrityError,
    SasUrlRequestError,
    SasUrlResponse,
    TokenExpiredError,
)

FIXTURES_DIR = ROOT_DIR / "fixtures"
VALID_FIXTURE_HASH = "2cbddf7b997f39a22f77178ac0d67a6ee7a420b8d3cca81b18b74169fe9f59a9"
VALID_FIXTURE_SIZE = 1005


class TestIMercadoClient(unittest.TestCase):
    """Bateria de testes para o cliente desacoplado da API iMercado Conciliação."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="atlas_test_imercado_")
        self.destination_path = Path(self.temp_dir) / "downloaded_imbarq006.txt"
        self.client = IMercadoClient(mock_mode=True, fixtures_dir=FIXTURES_DIR)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # -----------------------------------------------------------------------
    # 1. Teste de fluxo completo (Auth -> SAS URL -> Download com Hash)
    # -----------------------------------------------------------------------

    def test_full_flow_authentication_sas_and_download(self) -> None:
        """Valida a jornada completa FL-001 em modo desacoplado com verificação estrita."""
        # 1. Autenticação OAuth 2.0
        token = self.client.authenticate(
            client_id="PARTICIPANTE_105",
            client_secret="SECRET_SUPER_SECRETO_B3",
        )
        self.assertIsInstance(token, AuthToken)
        self.assertTrue(token.access_token.startswith("mock_token_"))
        self.assertEqual(token.token_type, "Bearer")
        self.assertEqual(token.expires_in, 3600)
        self.assertFalse(token.is_expired())
        self.assertIn("Bearer mock_token_", token.authorization_header)

        # 2. Solicitação de SAS URL
        sas_response = self.client.request_sas_url(
            token=token,
            report_type="IMBARQ006",
            movement_date=date(2026, 11, 3),
            participant_code=105,
        )
        self.assertIsInstance(sas_response, SasUrlResponse)
        self.assertTrue(sas_response.sas_url.startswith("mock://"))
        self.assertEqual(sas_response.report_type, "IMBARQ006")
        self.assertEqual(sas_response.movement_date, "2026-11-03")
        self.assertEqual(sas_response.participant_code, "105")
        self.assertEqual(sas_response.file_name, "IMBARQ006_105_20261103.txt")
        self.assertEqual(sas_response.file_size, VALID_FIXTURE_SIZE)
        self.assertEqual(sas_response.sha256_hash, VALID_FIXTURE_HASH)

        # 3. Download com verificação de SHA-256
        download_result = self.client.download_report(
            sas_url=sas_response.sas_url,
            destination_path=self.destination_path,
            expected_hash_sha256=sas_response.sha256_hash,
            max_retries=3,
        )

        self.assertIsInstance(download_result, DownloadResult)
        self.assertEqual(download_result.destination_path, self.destination_path.resolve())
        self.assertEqual(download_result.bytes_downloaded, VALID_FIXTURE_SIZE)
        self.assertEqual(download_result.sha256_hash, VALID_FIXTURE_HASH)
        self.assertEqual(download_result.attempts, 1)
        self.assertTrue(self.destination_path.exists())

        # 4. Verificação de interoperabilidade: o arquivo baixado é consumível pelo parser FL-002
        parsed_file = parse_imbarq006(self.destination_path)
        self.assertEqual(parsed_file.header.identificador_layout, "IMBARQ006")
        self.assertEqual(parsed_file.header.codigo_participante, 105)
        self.assertEqual(len(parsed_file.detalhes), 3)
        self.assertEqual(parsed_file.trailer.total_registros_arquivo, 5)
        self.assertEqual(parsed_file.trailer.total_registros_detalhe, 3)

    # -----------------------------------------------------------------------
    # 2. Teste de retry em falhas transitórias de rede
    # -----------------------------------------------------------------------

    def test_download_retry_on_transient_network_failure(self) -> None:
        """Valida que falhas transitórias de transporte acionam retries até o sucesso."""
        token = self.client.authenticate("PARTICIPANTE_105", "SECRET_B3")
        sas_response = self.client.request_sas_url(
            token=token,
            report_type="IMBARQ006",
            movement_date="20261103",
            participant_code="00000105",
        )

        # Simula 2 falhas transitórias (Connection reset e Timeout), sucedidas por sucesso na 3ª tentativa
        simulated_errors = [
            urllib.error.URLError("Connection reset by peer"),
            TimeoutError("The read operation timed out"),
        ]
        self.client.register_mock_failures(sas_response.sas_url, simulated_errors)

        download_result = self.client.download_report(
            sas_url=sas_response.sas_url,
            destination_path=self.destination_path,
            expected_hash_sha256=sas_response.sha256_hash,
            max_retries=3,
            retry_delay=0.001,  # Delay mínimo para execução ultrarrápida do teste
        )

        # O download deve ter sido concluído com sucesso na 3ª tentativa
        self.assertEqual(download_result.attempts, 3)
        self.assertEqual(download_result.bytes_downloaded, VALID_FIXTURE_SIZE)
        self.assertEqual(download_result.sha256_hash, VALID_FIXTURE_HASH)
        self.assertTrue(self.destination_path.exists())

    def test_download_exhausted_retries_raises_download_error(self) -> None:
        """Garante que quando todas as tentativas falham, DownloadError é lançado."""
        token = self.client.authenticate("PARTICIPANTE_105", "SECRET_B3")
        sas_response = self.client.request_sas_url(
            token=token,
            report_type="IMBARQ006",
            movement_date="2026-11-03",
            participant_code=105,
        )

        # Simula 3 falhas para max_retries=3
        self.client.register_mock_failures(
            sas_response.sas_url,
            [
                urllib.error.URLError("Falha de rede 1"),
                urllib.error.URLError("Falha de rede 2"),
                urllib.error.URLError("Falha de rede 3"),
            ],
        )

        with self.assertRaises(DownloadError) as ctx:
            self.client.download_report(
                sas_url=sas_response.sas_url,
                destination_path=self.destination_path,
                max_retries=3,
                retry_delay=0.001,
            )

        self.assertIn("Falha ao realizar download após 3 tentativas", str(ctx.exception))
        # Garante que nenhum arquivo parcial ou corrompido sobrevive no destino
        self.assertFalse(self.destination_path.exists())

    # -----------------------------------------------------------------------
    # 3. Teste de divergência de integridade SHA-256
    # -----------------------------------------------------------------------

    def test_hash_mismatch_raises_hash_mismatch_error(self) -> None:
        """Valida que hash SHA-256 divergente causa rejeição imediata do arquivo."""
        token = self.client.authenticate("PARTICIPANTE_105", "SECRET_B3")
        sas_response = self.client.request_sas_url(
            token=token,
            report_type="IMBARQ006",
            movement_date="2026-11-03",
            participant_code=105,
        )

        # Hash intencionalmente divergente / corrompido
        corrupted_expected_hash = "0000000000000000000000000000000000000000000000000000000000000000"

        with self.assertRaises(HashMismatchError) as ctx:
            self.client.download_report(
                sas_url=sas_response.sas_url,
                destination_path=self.destination_path,
                expected_hash_sha256=corrupted_expected_hash,
                max_retries=3,
                retry_delay=0.001,
            )

        err = ctx.exception
        self.assertEqual(err.expected_hash, corrupted_expected_hash)
        self.assertEqual(err.actual_hash, VALID_FIXTURE_HASH)
        self.assertIn("Divergência de integridade SHA-256", str(err))

        # O arquivo no caminho definitivo não deve ter sido criado
        self.assertFalse(self.destination_path.exists())

    # -----------------------------------------------------------------------
    # 4. Testes de validação de parâmetros e expiração de token
    # -----------------------------------------------------------------------

    def test_authenticate_requires_valid_credentials(self) -> None:
        """Valida rejeição de credenciais vazias ou inválidas."""
        with self.assertRaises(AuthenticationError):
            self.client.authenticate("", "secret")

        with self.assertRaises(AuthenticationError):
            self.client.authenticate("client_id", "")

        with self.assertRaises(AuthenticationError):
            self.client.authenticate("invalid", "invalid")

    def test_request_sas_url_validates_expired_token(self) -> None:
        """Valida rejeição quando o token Bearer estiver expirado."""
        expired_token = AuthToken(
            access_token="expired_jwt_token",
            token_type="Bearer",
            expires_in=3600,
            issued_at=time.time() - 4000,  # Emitido há mais de 1 hora
        )
        self.assertTrue(expired_token.is_expired())

        with self.assertRaises(TokenExpiredError):
            self.client.request_sas_url(
                token=expired_token,
                report_type="IMBARQ006",
                movement_date="2026-11-03",
                participant_code=105,
            )

    def test_request_sas_url_requires_token_and_params(self) -> None:
        """Valida obrigatoriedade de parâmetros essenciais para SAS URL."""
        with self.assertRaises(AuthenticationError):
            self.client.request_sas_url(
                token=None,  # Nenhum token ativo no cliente
                report_type="IMBARQ006",
                movement_date="2026-11-03",
                participant_code=105,
            )

        token = self.client.authenticate("atlas_105", "secret_pass")
        with self.assertRaises(SasUrlRequestError):
            self.client.request_sas_url(token, "", "2026-11-03", 105)

        with self.assertRaises(SasUrlRequestError):
            self.client.request_sas_url(token, "IMBARQ006", "2026-11-03", "")

    # -----------------------------------------------------------------------
    # 5. Testes em modo HTTP real com urllib mockado
    # -----------------------------------------------------------------------

    @patch("urllib.request.urlopen")
    def test_http_mode_oauth_and_sas_and_download(self, mock_urlopen: MagicMock) -> None:
        """Valida as chamadas HTTP reais via urllib (mock_mode=False)."""
        real_client = IMercadoClient(base_url="https://api.imercado.b3.com.br", mock_mode=False)

        # 1. Mock de resposta de autenticação OAuth
        mock_auth_response = MagicMock()
        mock_auth_response.status = 200
        mock_auth_response.read.return_value = json.dumps({
            "access_token": "real_jwt_token_12345",
            "token_type": "Bearer",
            "expires_in": 7200,
        }).encode("utf-8")
        mock_auth_response.__enter__.return_value = mock_auth_response

        mock_urlopen.return_value = mock_auth_response
        token = real_client.authenticate("client_b3", "secret_b3")
        self.assertEqual(token.access_token, "real_jwt_token_12345")
        self.assertEqual(token.expires_in, 7200)

        # 2. Mock de resposta de solicitação de SAS URL
        mock_sas_response = MagicMock()
        mock_sas_response.status = 200
        mock_sas_response.read.return_value = json.dumps({
            "sas_url": "https://b3storage.blob.core.windows.net/conciliacao/IMBARQ006.txt?sig=xyz",
            "file_name": "IMBARQ006_105_20261103.txt",
            "file_size": 1005,
            "sha256_hash": VALID_FIXTURE_HASH,
        }).encode("utf-8")
        mock_sas_response.__enter__.return_value = mock_sas_response

        mock_urlopen.return_value = mock_sas_response
        sas_res = real_client.request_sas_url(token, "IMBARQ006", "2026-11-03", 105)
        self.assertEqual(sas_res.file_name, "IMBARQ006_105_20261103.txt")
        self.assertEqual(sas_res.sha256_hash, VALID_FIXTURE_HASH)

        # 3. Mock de download HTTP via stream
        valid_content = (FIXTURES_DIR / "imbarq006-valid.txt").read_bytes()
        mock_dl_response = MagicMock()
        mock_dl_response.status = 200
        mock_dl_response.headers = {"Content-Length": str(len(valid_content))}
        mock_dl_response.read.side_effect = [valid_content, b""]
        mock_dl_response.__enter__.return_value = mock_dl_response

        mock_urlopen.return_value = mock_dl_response
        dl_res = real_client.download_report(
            sas_url=sas_res.sas_url,
            destination_path=self.destination_path,
            expected_hash_sha256=VALID_FIXTURE_HASH,
        )
        self.assertEqual(dl_res.bytes_downloaded, len(valid_content))
        self.assertEqual(dl_res.sha256_hash, VALID_FIXTURE_HASH)
        self.assertTrue(self.destination_path.exists())

    @patch("urllib.request.urlopen")
    def test_http_mode_content_length_mismatch(self, mock_urlopen: MagicMock) -> None:
        """Valida detecção de Content-Length divergente em requisição HTTP."""
        real_client = IMercadoClient(mock_mode=False)

        mock_dl_response = MagicMock()
        mock_dl_response.status = 200
        mock_dl_response.headers = {"Content-Length": "2000"}  # Esperado 2000 bytes
        mock_dl_response.read.side_effect = [b"Apenas 50 bytes recebidos...", b""]
        mock_dl_response.__enter__.return_value = mock_dl_response
        mock_urlopen.return_value = mock_dl_response

        with self.assertRaises(DownloadError) as ctx:
            real_client.download_report(
                sas_url="https://storage.b3.com.br/file.txt",
                destination_path=self.destination_path,
                max_retries=1,
                retry_delay=0.001,
            )

        self.assertIn("Content-Length", str(ctx.exception))
        self.assertFalse(self.destination_path.exists())


if __name__ == "__main__":
    unittest.main()
