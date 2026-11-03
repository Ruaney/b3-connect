# Método, decisões e critérios

## Decisões de 2026-11-03

Confirmadas pelo usuário: público inicial de tecnologia institucional; pós-negociação de listados como primeiro recorte; cobertura por fluxo; operações/back office e demais frentes preservados no roadmap. Objetivo comercial futuro: administrar integrações sem impedir a operação independente do núcleo aberto.

AI Memory: nenhum MCP correspondente foi encontrado entre as ferramentas expostas nesta sessão. Este registro local é provisório e não equivale a sincronização no AI Memory.

## Unidade de cobertura

Fluxo é uma capacidade operacional com resultado verificável. Os oito registros iniciais são candidatos à decomposição após leitura técnica. Um arquivo não equivale automaticamente a um fluxo completo. Não dividir fluxos para inflar progresso: registrar alterações de granularidade e recalcular o inventário inteiro.

Para cada estado: quantidade de fluxos que satisfazem o critério / quantidade de fluxos conhecidos nesta versão. Não usar média entre estados nem somá-los. Mapeamento de 100% dos registros cadastrados é uma propriedade do catálogo, não prova de descoberta completa.

- Mapeado: finalidade, sistema, fonte e pendências registrados.
- Documentado: jornada, pré-requisitos, entradas/saídas, erros, dependências e aceite revisados com fontes técnicas.
- Implementado: código disponível com interface e exemplos; não exige aprovação B3 para ser contado neste estado.
- Testado localmente: testes executados e evidência referenciando versão de código e fixtures sintéticas.
- Validado B3: evidência de teste autorizado com ambiente, data, versão e limitações; não significa certificação oficial.

Os estados são independentes; cada afirmação verdadeira exige evidência própria. Ao avançar além de mapeado, adicionar referências a evidências no registro. Hoje todos os demais estados são falsos.

## Descoberta e coleta

Registrar fontes consultadas e apenas localizadas separadamente. Verificar os portais institucionais, de desenvolvedores, de normas, conectividade e certificação; a lista atual cobre somente uma parte desse universo. Não atribuir percentual à descoberta enquanto o universo for desconhecido.

Na etapa 2, resolver links de anexos oficiais, registrar URL final, data, versão declarada, tipo, hash SHA-256 e condição de redistribuição. Preservar versões em vez de sobrescrever. Extrair PDF/HTML e identificar arquivos compactados e schemas sem executar conteúdo baixado. Dados e tokens institucionais ficam fora do repositório. Tratar anexos inacessíveis como pendências, sem inferir conteúdo.

## Próximo marco

Revisar contratação e jornada do iMercado, catálogo vigente de download e layouts de posições. O IMBARQ001 foi descontinuado em 30/06/2024 e o piloto foi fixado no IMBARQ006 (processamento noturno da câmara) e automação de download via API iMercado, documentado em [pilot-imbarq006.md](pilot-imbarq006.md). Encerrar o marco com requisitos, critérios de teste, dependências de acesso e decisão justificada. Não criar conectores antes disso.

Antes de publicar: escolher licença do código e verificar a política de distribuição de materiais de terceiros. Não inferir que o acesso público autoriza republicação integral.
