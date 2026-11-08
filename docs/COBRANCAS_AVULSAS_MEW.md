# MEW — Cobranças Avulsas / Contrato e Pagamento

## Finalidade
Rota administrativa: \`/mew/cobrancas-avulsas\`.
Rota pública individual: \`/cobranca/<token>\`.

Esta modalidade é **independente** do cadastro acadêmico existente. Um cliente não precisa ter
matrícula, RA ou credenciais de aluno para ler o contrato, capturar foto, assinar e visualizar
a forma de pagamento. Nenhuma matrícula/certificação é efetivada por esta funcionalidade.

## Fluxo implementado
1. Operador com sessão \`mew_admin\` preenche nome, CPF válido, e-mail, endereço opcional,
   objeto/descrição de serviço, valor, parcelas, juros e número de contrato (ou automático).
2. Informa **link PicPay oficial** \`https://link.picpay.com/p/...\` OU envia uma imagem
   PNG/JPEG/WebP de QR Code. O QR é armazenado **privadamente no Cloudflare R2**.
3. O servidor persiste o HTML individualizado e seu SHA-256, e cria uma URL com token aleatório
   de alta entropia. A URL é copiável no painel MEW.
4. Contratante lê o instrumento no visualizador integrado, faz duas declarações distintas,
   captura/seleciona fotografia e desenha assinatura no canvas.
5. O servidor valida CSRF, aceita imagens reais com limites de tamanho, rejeita assinatura
   vazia, remove metadados EXIF, calcula hashes e armazena foto e assinatura no R2.
6. Após o aceite gravado, a mesma URL passa a exibir QR Code ou o checkout PicPay oficial.
7. O administrador consulta contratos, evidências e, **após comprovar recebimento**, registra
   baixa manual com valor conferido e identificação da transação.

## Configuração / implantação
- PostgreSQL: executar \`python migrate.py\`; cria tabela \`cobrancas_avulsas\` sem apagar as existentes.
- Cloudflare R2: precisam estar configuradas as mesmas variáveis e permissões usadas nos
  contratos digitais do sistema (\`R2_...\` e aliases vigentes em \`r2_storage.py\`).
- Opcional: \`PUBLIC_BASE_URL=https://sigeueducacional.com.br\`. Usado para montar links externos.
- A modalidade de **link manual/QR upload não exige credenciais de API do PicPay**.
  As credenciais OAuth do PicPay são pertinentes à outra modalidade, de criação automática
  de links em \`picpay_mew.py\`, que permanece separada nesta branch.
- Não publicar produção sem testes reais de upload R2, criação PostgreSQL, webcam e assinatura
  em celular, checkout externo e baixa administrativa.

## Limitações importantes
- O iframe do \`link.picpay.com\` **pode ser bloqueado pelo PicPay ou navegador** por
  políticas de anti-clickjacking. Sempre há botão externo do checkout oficial.
  Não se implementa proxy, clonagem nem remoção das proteções de terceiros.
- **Não é possível alterar via iframe o nome legal do recebedor** do PicPay.
  A identificação SIGEU aparece apenas na página do sistema, que não esconde o beneficiário real.
  A personalização do *nome do link* deve ser feita no painel PicPay.
- Campos de parcelamento/juros descrevem o acordo: **não alteram um link PicPay previamente
  criado**. O operador deve confirmar o mesmo número de parcelas e o mesmo valor no PicPay.
- Links e QR Codes fornecidos manualmente não geram eventos de pagamento autenticados
  automaticamente. Clique, retorno à página, upload de comprovante ou assinatura **não**
  significam recebimento. A baixa automática requer uma futura conciliação API com transações
  verificadas pelo próprio provedor, associadas ao número do contrato e valor.
- Fotos e assinaturas são elementos probatórios eletrônicos; **não** representam reconhecimento
  facial, assinatura qualificada ICP-Brasil ou validação automática de identidade.
- O contrato novo é uma **adaptação para contratação avulsa do modelo institucional já existente**,
  sem criar RA fictício. A redação e o uso do nome de instituições certificadoras precisam
  de revisão jurídica/comercial antes da produção. Não reproduzir promessa de certificação quando
  o serviço específico não estiver sujeito a ela.

## Testes automatizados
\`python -m unittest discover -s tests -p 'test_cobrancas_avulsas.py' -v\`

CI: \`.github/workflows/picpay-mew-tests.yml\`; testa também a integração MEW PicPay anterior.

## Privacidade
- Não armazenar CPF, foto ou assinatura em links públicos ou no HTML do checkout.
- Não colocar chaves do PicPay nem credenciais do R2 em GitHub, navegador ou mensagens.
- A tela de contrato assinada do cliente exibe apenas data/hash; foto e grafia ficam
  acessíveis por URLs temporárias **somente ao administrador autenticado**.
- A política institucional deverá prever retenção, eliminação, acesso e resposta a titulares
  de dados em conformidade com a LGPD.


## Dois rascunhos pré-preparados para o deploy de novembro/2026
Na execução de \`python migrate.py\`, \`seed_rascunhos_cobranca_avulsa()\` cria
somente se não existirem (chave única \`numero_contrato\`):
- \`SIGEU-AV-DISC-95984\`: duas disciplinas (Fundamentos da Psicologia Positiva;
  Psicologia e Cognição no Trânsito), 80h cada, preço R$ 959,84, 8x de R$119,98,
  link PicPay informado pelo contratante. Condição sem juros deve ser conferida
  no checkout PicPay antes da publicação.
- \`SIGEU-AV-QR-50900\`: valor R$509,00, 1 parcela provisória, QR pendente,
  serviço e dados do cliente a confirmar.

**Privacidade:** os scripts de migração não inserem nomes, CPFs, endereços, imagens de
documentos ou e-mails pessoais no repositório GitHub. O operador completa os
dados somente no painel MEW, sob sessão administrativa. Não se deve preencher
identidade real a partir de fotografias sem conferência documental e autorização.

### Publicação dos rascunhos
1. Faça login no MEW e abra **Cobranças Avulsas**.
2. Clique **Completar e publicar** no rascunho desejado.
3. Preencha identificação e serviço. Para e-mail \`RA@sigeu.com\`, informe o
   número real da matrícula/RA e marque a opção correspondente.
4. Para QR, anexe a foto do código quando disponível.
5. Confira que o pagamento real tem mesmo valor, número de parcelas e juros
   do contrato. Marque a conferência e clique **Publicar**.
6. Copie o link individual gerado. O cliente lerá o contrato e poderá baixar PDF,
   autorizar registro fotográfico, capturar foto e assinar; só então verá o pagamento.

Não compartilhar o link do rascunho (ele responde HTTP 404 e não exibe o checkout).
A primeira tela não deve prometer certificação automática. A emissão de documentos
acadêmicos continua condicionada aos procedimentos institucionais reais.

### PDF
O contrato é gerado em HTML fixado com SHA-256 na publicação e pode ser exportado
para PDF via WeasyPrint, tanto pelo administrador quanto pelo cliente detentor do
link individual. Após a assinatura, a visualização administrativa contém foto,
assinatura, carimbo de data/hora e hashes de evidência. O PDF pré-assinatura
continua identificado como pendente, e não representa documento assinado.
