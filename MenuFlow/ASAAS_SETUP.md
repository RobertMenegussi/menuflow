# Configuração rápida do Asaas no MenuFlow

## 1) Criar a conta
- Crie a conta no Asaas
- Gere a API Key no ambiente desejado (Sandbox ou Produção)

## 2) No MenuFlow > Admin > Configurações
Preencha:
- Ativar Asaas = Sim
- Ambiente = Sandbox ou Produção
- API Key do Asaas
- URL pública do webhook
- Token do webhook
- Email para alertas do webhook

## 3) Salvar e cadastrar webhook
- Clique em **Salvar**
- Clique em **Cadastrar webhook no Asaas**

## 4) Endpoint do webhook
O endpoint do MenuFlow é:
- `/webhooks/asaas`

Exemplo de URL completa:
- `https://seu-dominio.com/webhooks/asaas`

## 5) Como funciona depois de configurado
- O cliente gera o Pix no celular
- O MenuFlow cria a cobrança Pix no Asaas
- O Asaas envia o evento de pagamento para o MenuFlow
- O MenuFlow confirma automaticamente o pagamento e atualiza a comanda

## 6) Observação importante
Se o webhook ainda não estiver configurado, o MenuFlow também tenta consultar o status da cobrança no Asaas durante o acompanhamento do pagamento.


Nota: quando o Asaas estiver ativo, o cliente deve informar CPF ou CNPJ do pagador para o Pix automático ser gerado.
