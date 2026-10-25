# Consolidação v4 + v5

Base: v5 (interface escura, login administrativo e atendimento humano completo).

## Incorporado do v4
- Tokens JWT HS256 com expiração configurável (`JWT_SECRET`, `TOKEN_TTL_S`), quando segredo está configurado; compatibilidade legada local sem segredo.
- Isolamento opcional por cliente via `CUSTOMER_AUTH_REQUIRED=true`. **Requer um gateway/SSO confiável**: ativar sem provedor de identidade bloqueia a autenticação silenciosa do navegador.
- Auditoria persistente das operações de assumir/responder/encerrar handoff e reset; consulta autenticada `/api/admin/audit`.
- Cabeçalhos de segurança, erros HTTP em JSON, rota `/api/health` compatível com v4.
- Acessibilidade: skip link, chat com semântica de log, limite de entrada, focos visíveis e movimento reduzido.
- Preferência de aviso de handoff registrada no histórico (`/api/handoff/notify`), sem notificação externa real.
- Diagrama original `.drawio`.

## Preservado do v5
- Interface preta/vermelha com landing page; login com cookie HttpOnly.
- Dashboard temporal, chat aprimorado, atendimento humano funcional, volume Railway.

## Atenção
`/api/auth/silent` continua demonstrativo em modo padrão; não representa SSO real. Não use identidades simuladas para clientes reais. O RBAC por chave de API do v4 não foi incorporado pois conflita com o login por cookie e o frontend do v5; o acesso administrativo permanece por sessão.
