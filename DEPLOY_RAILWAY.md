# ClaroUnifyHub — atualização do Railway

## Antes do deploy

1. No Railway > Serviço > Variables, configure:
   - `ADMIN_PASSWORD` = senha administrativa forte e exclusiva (não inclua em arquivos Git).
   - `ADMIN_SESSION_SECRET` = string aleatória com no mínimo 32 caracteres. Exemplo de geração no PowerShell: `[Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))`
   - `APP_ENV` = `production`
   - `ADMIN_AUTH_DISABLED` = `false`
   - `USE_LLM` = `false` se o Railway não tiver acesso ao Ollama. O fallback por regras continua funcionando.
   - `DATABASE_PATH` = `/data/clarounify.db`
2. Em Railway > Service > Volumes, monte volume persistente em `/data`. Faça backup do banco anterior antes de mudar o caminho ou montar o volume: o volume novo é inicialmente vazio.
3. Atualize os arquivos no GitHub e faça deploy da branch vinculada. O Dockerfile usa a porta do ambiente `PORT` automaticamente.
4. Configure o healthcheck path `/health` nas configurações do serviço.
5. Acesse o site e clique em `Painel Claro (interno)` para abrir o login.

## Avisos de escopo

- Aplicação de demonstração: os clientes e seus dados são fictícios, e o seletor público `/api/demo/clientes` e os endpoints de cliente simulam autenticação. **Não use com dados pessoais reais.** Para uso real é necessário login de cliente, autorização por recurso, proteção anti-abuso e revisão LGPD.
- As mensagens e atribuições humanas ficam no SQLite; os eventos operacionais globais permanecem em memória. Para escala horizontal e alta disponibilidade é necessário migrar para um banco servidor e fila/event bus.
- O painel requer uma senha compartilhada por operadores, não tem ainda múltiplos perfis individuais. O cookie expira em 8 horas.
- O botão `reiniciar demo` requer autenticação administrativa. A API de campanha proativa também foi restrita ao painel.
- Não inclua `clarounify.db`, `.venv`, `.git` e `.env` no pacote enviado ao GitHub.

## Rodar localmente

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:ADMIN_PASSWORD='uma-senha-forte-exclusiva'
$env:ADMIN_SESSION_SECRET='gere-uma-string-aleatoria-de-32-ou-mais-caracteres'
$env:USE_LLM='false'
uvicorn main:app --reload
```

## Testes

```powershell
python -m pytest -q
```

## Novidades v4 incorporadas na v6

- `JWT_SECRET`: configure 32+ caracteres aleatórios para JWT assinado persistente (opcional).
- `TOKEN_TTL_S`: validade dos tokens (padrão 3600 segundos).
- `CUSTOMER_AUTH_REQUIRED=false`: mantenha desativado na demonstração pública; a ativação requer integração de gateway/SSO e adaptação do frontend para Bearer tokens.
- Auditoria: `GET /api/admin/audit` com login administrativo, persistido no SQLite.
- Preferência de aviso de fila é uma simulação; não envia mensagens externas.
- RBAC `X-API-Key` do v4 não foi mantido: v6 preserva login por senha/cookie do v5.
