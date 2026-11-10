# Proteção e recuperação dos registros SIGEU

## Diagnóstico e cautela
O SiGEU usa **PostgreSQL** pela variável `DATABASE_URL`, não SQLite local. Nunca
substituir `DATABASE_URL` por um banco recém-criado para tentar "recuperar" contratos:
isso apenas apontaria a aplicação para outra base. Identificar a origem atual por
contagens e `database_id` (SHA-256 truncado da identificação técnica, sem senha).
O painel protegido fica em `/mew/backups`.

A listagem de cobranças está em `/mew/cobrancas-avulsas`; ela consulta as últimas
60 entradas de `public.cobrancas_avulsas`. Os contratos de alunos comuns vivem em
`public.contratos_alunos`. Diferenciar as duas tabelas ao diagnosticar dados.

## O que a cópia contém
- Exportação **somente leitura** em transação `REPEATABLE READ` de todas as tabelas
  de usuário no esquema `public` em CSV PostgreSQL nativo, com colunas e tipos.
- Arquivo `manifest.json` com identificação opaca do banco, data UTC, nome das
  tabelas, número de linhas, SHA-256 por CSV, sequências e commit da aplicação.
- TAR.GZ é cifrado localmente com **AES-256-GCM** (nonce aleatório e tag) antes
  do upload. O objeto criptografado é armazenado no Cloudflare R2 usando chave
  única e privada, sem URL pública. SHA-256 e tamanho são conferidos via `HEAD`.
- Não modifica, restaura, substitui nem elimina dados do banco. Sem retenção
  automática destrutiva; as cópias são imutáveis e recebem chaves distintas.
- O R2 também pode conter os PDFs, fotos e assinaturas já armazenados por outros
  componentes; o snapshot do PostgreSQL armazena as respectivas chaves/metadata,
  **não duplica os objetos binários do R2**. Esses devem manter o bucket original
  privado e contar com retenção/backups independentes.

## Configuração do serviço web no Render
As variáveis permanecem *somente* nos ambientes, nunca no Git:

- `DATABASE_URL`: conexão PostgreSQL já existente (não alterar sem recuperação).
- `R2_*`: bucket/conta/chaves já utilizadas pelo SiGEU.
- `SIGEU_BACKUP_KEY`: chave aleatória de 32 bytes em Base64 URL-safe.
  **Se essa chave se perder, as cópias criptografadas não poderão ser restauradas**.
  Guardar também em um gerenciador de segredos seguro e separado.
- `SIGEU_BACKUP_ON_DEPLOY=1`: captura e confirma cópia **antes** das
  migrações na inicialização do serviço; falha aborta a nova implantação,
  evitando prosseguir sem a proteção solicitada.
- `SIGEU_BACKUP_TRIGGER_TOKEN`: segredo independente para acionar backups
  via POST em `/internal/sigeu/backup/v1`; não aparece em templates/logs.

O painel `/mew/backups` exige sessão `mew_admin` para diagnóstico e POST com CSRF
para gerar cópia manual. O endpoint interno de agendamento exige segredo forte
pelo header `X-SIGEU-Backup-Token` e não responde a GET.

## Execução agendada
O script `backup_trigger.py` aciona o serviço web usando duas variáveis:
`SIGEU_BACKUP_URL=https://sistema-escolar-0bad.onrender.com/internal/sigeu/backup/v1`
e `SIGEU_BACKUP_TRIGGER_TOKEN`.
É indicado para cron diário no Render (UTC). Um cron separado pode gerar
cobrança mensal mínima; aprovar o custo antes de ativar o recurso.

## Verificação e recuperação
1. Verificar a origem/contagens no painel, e o log `[SIGEU DB PRE-MIGRATE]`.
2. Exigir `[SIGEU BACKUP VERIFIED]` no deploy, com contagem de tabelas e
   snapshot confirmado no R2, não somente "backup iniciado".
3. Para conferir um arquivo, executar
   `python verify_sigeu_backup.py 'backups-sigeu/v1/.../arquivo.sigeu'`
   usando as credenciais R2 e a chave de backup. O verificador autentica AES-GCM
   e confere SHA-256 do objeto e dos CSV, sem gravar no banco.
4. Para restaurar: provisionar **um PostgreSQL novo, vazio e isolado**; recriar
   schema compatível com a versão do código, verificar os CSV e suas colunas,
   importar respeitando chaves estrangeiras e ajustar sequências. Testar a
   leitura de contratos, assinaturas, cobranças, alunos e relações. Somente
   depois de validar, planejar migração/virada de conexão. **Não existe
   restauração automática na aplicação.**
5. Os backups de banco **não substituem** backups/versões dos objetos do R2,
   nem uma política formal de retenção de dados pessoais conforme LGPD.

## Cuidado: banco externo
Se a instância PostgreSQL do SiGEU não aparecer em `Render > PostgreSQL`,
a aplicação pode estar usando outro provedor, definido por `DATABASE_URL`.
Comparar identidade e ambiente antes de criar qualquer banco ou copiar os dados.
