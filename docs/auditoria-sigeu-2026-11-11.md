# SIGEU — auditoria inicial do cadastro, PostgreSQL e armazenamento (11/11/2026)

> Escopo: análise estática do repositório e do site público. **Não corresponde a
> auditoria autenticada de produção**. Nenhum dado de aluno foi consultado,
> copiado, apagado ou modificado. A integração efetiva com Render/R2 ainda requer
> confirmação do workspace e das configurações reais.

## Evidências verificadas

- O sistema é uma aplicação Flask, com `app.py`, PostgreSQL via
  `db_pool.py`, migrações em `migrate.py`, e painel MEW em `/mew/*`.
- `init_db()` criava `dados_pessoais` sem os campos `nome_pai`,
  `nome_mae`, `naturalidade`, `nacionalidade`, `data_nascimento`,
  `sexo`, `estado_civil` e `email_alternativo`. As rotas administrativas
  consultam e inserem esses campos. Em banco sem atualização manual anterior,
  a listagem e o cadastro podem falhar com erro de coluna inexistente.
- A busca na listagem de alunos era feita pelo navegador apenas nas linhas
  carregadas da página. Com paginação, o operador não encontra nomes fora da
  página atual.
- O nome exibido não era link para edição, apesar de existir rota de edição.
- `/mew/deletar-aluno/<id>` aceitava GET para uma operação irreversível.
- `r2_storage.py` já contém funções de upload e leitura privada; o
  `migrate_storage_to_r2.py` já prevê migração de anexos, contratos, fotos,
  documentos e projetos. Isso **não comprova** que a conta R2 esteja ativa.
- `backup_sigeu.py` exporta dados e metadados de colunas das tabelas
  `public`, com criptografia. Não representa por si só um dump integral
  restaurável das definições, dependências, índices, triggers e demais
  objetos PostgreSQL.
- `backup_trigger.py` e `/internal/sigeu/backup/v1` existem para backups
  agendados; sua execução efetiva depende de configurações e infraestrutura
  que ainda não foram inspecionadas.
- `capitulos.video_url` e `capitulos.pdf_url` continuam sendo as referências
  às mídias nas disciplinas. Um link fixo para vídeo R2 é compatível quando
  existe endereço HTTPS estável e acesso adequado. Nunca persistir URL
  temporária assinada como link fixo de curso.

## Mudanças propostas no PR #7

1. Garantir oito colunas de dados pessoais via migração aditiva/idempotente.
2. Buscar no PostgreSQL por nome/RA/CPF, mantendo filtro entre páginas.
3. Permitir clicar no nome para abrir o cadastro.
4. Exigir POST e CSRF na exclusão administrativa.
5. Disponibilizar `backup_postgres_full_r2.py` para dump custom
   `pg_dump` (esquema + dados + objetos), AES-256-GCM, SHA-256 e conferência
   com `pg_restore --list`.
6. Adicionar testes e integração contínua.

## Pré-requisitos do backup integral

- `DATABASE_URL` apontando **para o banco real de produção**.
- `R2_ACCOUNT_ID` ou `R2_ENDPOINT_URL`, `R2_BUCKET_NAME`,
  `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` definidos em ambiente
  protegido (nunca no GitHub).
- `SIGEU_BACKUP_KEY` de 32 bytes, guardada também fora do serviço para
  possibilitar restauração futura.
- Executáveis compatíveis `pg_dump` e `pg_restore` no serviço/cron
  que executará o comando.
- Bucket privado, política de retenção, alertas de falha e **cópia externa**
  do backup para evitar perda por exclusão acidental/comprometimento da conta.

Execução manual em ambiente autorizado:

```sh
python backup_postgres_full_r2.py --create
python backup_postgres_full_r2.py --verify "backups-postgres-full/v1/CHAVE_REAL.sigeu"
```

O teste acima verifica integridade criptográfica e legibilidade do arquivo.
**Também é obrigatório ensaiar um `pg_restore` em banco isolado**, antes de
considerar a recuperação operacionalmente validada. O script não restaura
na base de produção e não agenda cron automaticamente.

## Fluxo operacional seguro pendente

1. Confirmar workspace Render e mapear web service, Postgres, variáveis
   **pelos nomes** (não divulgar valores sigilosos), logs e deploy ativo.
2. Conferir `information_schema.columns` e contagens por tabela **sem
   expor nomes, CPF ou emails de estudantes**.
3. Identificar todos os objetos existentes no R2, backup recente e se
   existe verificação de recuperação.
4. Criar e verificar dump integral antes de migrações em produção.
5. Executar testes de cadastro, busca, edição, matrícula/disciplina,
   financeiro, documentos e portal do aluno em homologação.
6. Somente então publicar a correção e conferir logs/saúde após o deploy.
7. Inventariar mídia antiga (PDFs, contratos e vídeos) e copiar ao R2,
   inicialmente sem excluir origem; checar integridade e permissões.
8. Programar backup integral periódico e teste de restauração recorrente.

## Riscos adicionais a tratar separadamente

- A rota de novo aluno sobrescreve a senha informada com o CPF quando
  o CPF contém onze dígitos. Exige plano de alteração e comunicação dos
  mecanismos de primeiro acesso para evitar bloqueio dos alunos.
- Ausência de `FLASK_SECRET_KEY` estável causa troca da chave de sessão
  entre reinícios (há fallback aleatório no código).
- Outras rotas legadas administrativas merecem auditoria de operações
  destrutivas, autorização e CSRF antes de uma revisão completa.
- O backup do PostgreSQL não inclui os arquivos que já estejam no R2.
  É necessário inventário e proteção independente dos objetos.
- URLs privadas assinadas do R2 expiram: para vídeo persistente, usar
  domínio de mídia adequado ou endpoint autenticado que gere URL sob demanda.

**Não efetuar `--purge-db`, não apagar arquivos antigos e não reconstruir
tabelas existentes antes de verificar backup e restauração.**
