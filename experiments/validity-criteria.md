# Critérios de validade de runs

## Estados
- `valid`: configuração, aliases, agenda e dados atendem ao protocolo.
- `invalid-apparatus`: falha instrumental demonstrada (versão/configuração errada, dados brutos perdidos, gerador incapaz de cumprir agenda, run concorrente, alvo da composição não verificado).
- `valid-system-anomaly`: instrumento correto, mas SUT apresentou erro, timeout, throttling, cold start tardio ou latência acima de Lmax.

## Não são motivos automáticos para invalidar
Erros HTTP/5xx, timeout da aplicação, throttling Lambda, latência acima de Lmax, p95 ruim ou cold starts durante medição são resultados do sistema e devem ser preservados.

## Checks mínimos
1. Plano, condição, hashes e config registrados.
2. `target-verification.json` existe, `ok == true` e `composition` corresponde à condição. A integração da rota da composição aponta para o alias `benchmark` da função esperada e o alias está na versão registrada em `cold-reset.json`.
3. Nenhuma invocação da aplicação entre cold reset e T0.
4. Agenda e contagens dentro da tolerância; dropped iterations investigadas.
5. JSON bruto k6, amostras por requisição, logs Lambda, métricas e custo presentes.
6. `START`/`REPORT` associados por `RequestId`; logs incompletos são falha de coleta.
7. Sem outro run ativo.
8. Dataset e documento permanecem imutáveis.
9. Todos os artefatos são preservados mesmo se inválido.

## Composição decomposta — verificação adicional
Os primeiros `REPORT` de cada worker após o cold reset devem conter `Init Duration`. Ausência de `Init Duration` no primeiro `REPORT` do worker indica que a invocação não passou pelo alias `benchmark` (por exemplo, o orquestrador chamou `FunctionName=worker` sem qualificador). Nesse caso o run é `invalid-apparatus`.

## Composição monolítica ↔ decomposta — estrutura de API
O experimento usa duas HTTP APIs do mesmo stack (`MonolithApi` e `DecomposedApi`), cada uma
com a rota `GET /documents/{id}/analysis` integrada ao alias `benchmark` da função
correspondente. A comparação entre composições assume que o overhead de gateway é
fornecido pelo mesmo serviço AWS a partir do mesmo template. Qualquer divergência de
configuração de gateway entre as duas APIs (throttle, CORS, authorizer, mapeamento)
deve ser registrada no TCC antes da campanha; se houver divergência, o efeito de
composição não pode ser interpretado isoladamente.

## Verificação de alvo por run
O run é `invalid-apparatus` se `target-verification.json` estiver ausente, tiver `ok != true`,
ou se `composition` não corresponder à condição. Também é `invalid-apparatus` se algum
`REPORT` de worker do caminho decomposto não exibir `Init Duration` na primeira
invocação após o cold reset — isso indica que a chamada saiu sem qualificador de alias.