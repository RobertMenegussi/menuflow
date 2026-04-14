# MenuFlow • versão pronta para Pix automático com Asaas

Fluxo principal desta versão:

- Cliente escolhe itens no cardápio e envia pedido por mesa
- A própria tela do cliente pode consultar a comanda da mesa
- O cliente pode pagar via Pix pelo celular
- O admin controla pedidos, comandas, pagamentos, histórico, relatórios e recibos
- O sistema já está preparado para baixa automática via Asaas + webhook
- Se o Asaas ainda não estiver configurado, o Pix manual por chave continua disponível como fallback

## Rodar

- `start_local.bat`
- ou `start_public_tunnel.bat`

## Entradas

- Cliente: `/client`
- Admin: `/admin`
- Login admin: `admin` / `admin123`
- Webhook Asaas: `/webhooks/asaas`

## Configuração do Asaas no MenuFlow

1. Abra o admin em **Configurações**
2. Preencha:
   - **Ativar Asaas = Sim**
   - **Ambiente = Sandbox ou Produção**
   - **API Key do Asaas**
   - **URL pública do webhook**
   - **Token do webhook**
   - **Email para alertas do webhook**
3. Clique em **Salvar**
4. Clique em **Cadastrar webhook no Asaas**

## Observações importantes

- O MenuFlow tenta confirmar pagamentos automaticamente via webhook do Asaas
- Enquanto o webhook não estiver configurado, o sistema ainda tenta consultar o status da cobrança no Asaas durante o acompanhamento do Pix
- O Pix manual por chave continua funcionando como reserva, caso o Asaas esteja desativado
- Para produção, use uma URL pública estável (domínio ou túnel fixo). Links aleatórios do trycloudflare servem para teste, mas mudam com frequência

## Fluxo sugerido de teste

1. No cliente, informe a mesa e envie um pedido
2. Gere um Pix pela tela do cliente
3. Pague a cobrança no Asaas Sandbox/Produção
4. Veja o status mudar automaticamente no MenuFlow
5. Confira a baixa em **Pagamentos**, **Comandas**, **Histórico** e **Relatórios**


Nota: quando o Asaas estiver ativo, o cliente deve informar CPF ou CNPJ do pagador para o Pix automático ser gerado.
