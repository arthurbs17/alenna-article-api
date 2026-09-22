# Improve Article API

Solução para o desafio técnico da Alenna: aplicar ao conteúdo de um artigo as sugestões de
tradução/revisão (geradas por IA) que o usuário **aceitou** ou **rejeitou**.

> **Entrada:** artigo (dividido em trechos) + sugestões + decisões do usuário
> **Saída:** artigo atualizado + o status de cada sugestão + sugestões pendentes com offsets ajustados

A regra fica toda em [`alenna/engine.py`](alenna/engine.py): uma função pura, sem dependências
externas. A API HTTP ([`alenna/http.py`](alenna/http.py)) é só uma casca fina por cima dela.

---

## 1. Como pensei o problema

"Aplicar uma sugestão" parece um `str.replace`. A dificuldade está no que acontece em volta:

1. **Várias sugestões no mesmo trecho.** Aplicar a primeira muda o tamanho do texto, e os offsets
   das outras passam a apontar para o lugar errado.
2. **Sugestões que disputam o mesmo texto.** Por exemplo, a tradução do trecho inteiro e a correção
   de uma palavra dentro dele. As duas não cabem juntas.
3. **O texto mudou depois que a sugestão foi gerada.** O usuário pode ter editado o artigo à mão
   entre a geração e a decisão.
4. **O usuário decide aos poucos.** O que ficou pendente precisa continuar valendo para o texto novo.
5. **Entrada imperfeita.** O frontend pode mandar decisões duplicadas ou ids que não existem.

Para os casos ambíguos, segui um princípio só:

> **Na dúvida, não altere o texto do pesquisador; informe o motivo.**
> Deixar de aplicar uma sugestão custa um clique. Aplicar no lugar errado quebra a confiança de
> que "aceitar" faz exatamente o que o usuário viu na tela, e num artigo científico pode passar
> despercebido.

O outro lado desse princípio: **uma sugestão problemática nunca bloqueia as outras do lote.**

---

## 2. Suposições e decisões

| # | Ponto em aberto | Decisão | Por quê |
|---|---|---|---|
| D1 | Representação do artigo | Lista ordenada de **trechos** (`id`, `text`). O texto completo é a concatenação dos trechos, e cada trecho guarda seus próprios separadores | O enunciado diz que o sistema já divide o texto em trechos. Com offsets locais a cada trecho, editar um trecho nunca desloca os offsets de outro |
| D2 | Como a sugestão aponta para o texto | `segment_id` + intervalo `[start, end)` + `original` (o texto esperado nesse intervalo) + `replacement` | Um formato só cobre revisão pontual, tradução do trecho inteiro (intervalo = trecho todo), inserção (`start == end`) e remoção (`replacement == ""`). O `original` permite perceber que o texto mudou |
| D3 | Sugestão que cruza trechos | Não existe | A IA gera sugestões por trecho |
| D4 | Unidade dos offsets | Code points (índice de `str` do Python) | É o que o backend Python usa. **Atenção:** em JS os índices são UTF-16, então emoji e alguns símbolos contam 2. O frontend precisa converter (há um teste mostrando isso) |
| D5 | Sugestão sem decisão | Fica **pendente**, e o texto não muda | O usuário pode decidir em lotes. Não decidir não é o mesmo que rejeitar |
| D6 | Ordem de aplicação | As edições de um trecho são aplicadas **juntas, contra o texto original**, num único passe ordenado por posição | Evita os offsets desalinhados. O resultado não depende da ordem em que as decisões chegam |
| D7 | Duas aceitas que se sobrepõem | **Nenhuma das duas é aplicada**. As duas voltam como `conflict`, com o id da outra. O resto do lote é aplicado normalmente | Escolher uma vencedora seria adivinhar o que o usuário quis. A UI pode mostrar o conflito e pedir para ele escolher |
| D8 | O que conta como sobreposição | `a.start < b.end and b.start < a.end`. Intervalos que **só se encostam não conflitam**. Uma inserção estritamente **dentro** de um intervalo conflita. **Duas inserções no mesmo ponto** conflitam | São regras determinísticas. O único caso em que a ordem entre as edições seria arbitrária (duas inserções no mesmo ponto) vira conflito |
| D9 | O texto mudou desde a geração | Se `text[start:end] == original`, aplica. Se não, tenta **realocar**: quando `original` aparece **exatamente uma vez** no trecho, usa essa posição. Se não aparece, ou aparece mais de uma vez, marca `stale` e não aplica | Edições manuais pequenas são comuns, e a realocação salva a maioria dos casos. Quando há mais de uma ocorrência, o risco de trocar a palavra errada é real |
| D10 | Inserção pura (`original == ""`) | Não há texto para procurar. Aplica se o offset estiver dentro do trecho; senão, `stale` | É uma limitação conhecida (ver seção 6) |
| D11 | Pendentes depois de aplicar outras | São **rebaseadas**: o offset é deslocado pela diferença de tamanho das edições aplicadas antes delas. Se o intervalo foi atingido por uma edição aplicada, viram `superseded` | Sem isso, a próxima rodada usaria offsets errados. O teste de propriedade garante que aplicar em várias rodadas dá o mesmo texto que aplicar tudo de uma vez |
| D12 | Decisões repetidas para a mesma sugestão | Se forem idênticas, não muda nada. Se forem contraditórias (aceitar e rejeitar, ou aceitar com edições diferentes), a sugestão vira `invalid` e não é aplicada | Isso é bug do cliente, então o caminho conservador é não aplicar. Descartei "a última vence" porque não há timestamp confiável |
| D13 | Decisão para uma sugestão que não existe | Entra em `errors` do resultado, e o lote continua | Um item ruim não derruba os outros |
| D14 | Sugestão malformada: trecho inexistente, id duplicado, offset negativo ou invertido, `len(original) ≠ end − start` | `invalid`, com o motivo | Com id duplicado, não dá para saber a qual cópia a decisão se refere. O tamanho que não bate indica que a sugestão foi montada errado |
| D15 | Artigo com id de trecho duplicado | `ValueError` (HTTP 400) | Nesse caso o artigo em si está corrompido, e não existe resultado que faça sentido |
| D16 | "Aceitar com ajuste" | `Decision.edited_replacement` opcional. `""` significa apagar o intervalo | É um fluxo comum em produtos de IA generativa: o usuário ajusta a sugestão antes de aceitar. O custo de suportar isso é uma linha |
| D17 | Estado e persistência | A função é **pura**: não altera a entrada e devolve o artigo novo, o status de cada sugestão e as pendentes rebaseadas. Quem chama cuida de persistir | Fica fácil de testar e de encaixar no backend real. Banco, fila e auditoria ficam de fora |
| D18 | Reenviar uma sugestão já aplicada | O chamador deve mandar só as pendentes (o resultado já devolve essa lista). Se reenviar, a sugestão normalmente vira `stale`, porque o `original` sumiu do texto | Risco: se o `original` ainda existir **uma única vez** em outro ponto do trecho, a realocação aplica de novo. Ver seção 6 |

### Status de cada sugestão

| Status | Significado |
|---|---|
| `applied` | Foi aceita e aplicada |
| `rejected` | O usuário rejeitou |
| `pending` | Ainda não tem decisão e continua válida (volta em `remaining_suggestions`) |
| `conflict` | Foi aceita, mas se sobrepõe a outra aceita, e nenhuma das duas foi aplicada |
| `stale` | O texto mudou e não deu para localizar a sugestão com segurança |
| `superseded` | Estava pendente, mas o intervalo dela foi alterado por uma sugestão aplicada |
| `invalid` | Entrada malformada; o motivo vem em `reason` |

---

## 3. A lógica

```
apply_decisions(article, suggestions, decisions) -> ApplyResult

1. indexa os trechos por id                        (erro se houver id duplicado, D15)
2. valida as sugestões                             -> invalid                    (D14)
3. agrupa as decisões por sugestão                 -> invalid / errors           (D12, D13)
4. para cada sugestão válida:
     rejeitada                                     -> rejected
     ancora no texto atual do trecho (D9/D10)      -> stale, se falhar
     aceita                                        -> candidata a aplicar
     sem decisão                                   -> pendente
5. para cada trecho:
     descarta as aceitas que se sobrepõem          -> conflict                   (D7/D8)
     ordena as restantes por (start, end) e monta
     o texto novo num único passe                  -> applied                    (D6)
     para cada pendente:
       sobrepõe alguma aplicada?                   -> superseded                 (D11)
       senão desloca pela soma dos deltas das
       aplicadas que terminam antes dela           -> pending (offsets novos)
6. devolve ApplyResult(article, results, remaining_suggestions, errors)
```

O centro da solução é montar o texto novo num único passe, com todos os offsets apontando para o
texto original:

```python
def _splice(text, edits):          # edits ordenadas e sem sobreposição
    parts, cursor = [], 0
    for edit in edits:
        parts.append(text[cursor:edit.start])
        parts.append(edit.replacement)
        cursor = edit.end
    parts.append(text[cursor:])
    return "".join(parts)
```

Como nenhum offset é calculado sobre um texto já editado, não sobra deslocamento acumulado para
corrigir. O custo é O(n log n) por trecho para ordenar e O(k²) para detectar conflitos, sendo k o
número de aceitas no trecho (poucas, na prática).

Arquivos:

| Arquivo | Conteúdo |
|---|---|
| `alenna/domain.py` | Modelos imutáveis (`Article`, `Segment`, `Suggestion`, `Decision`, `ApplyResult`...) |
| `alenna/engine.py` | `apply_decisions` e os auxiliares `_anchor`, `_overlaps`, `_drop_conflicts`, `_splice`, `_rebase` |
| `alenna/http.py` | FastAPI: `POST /articles/apply-suggestions` |
| `alenna/config.py` | Configuração lida do ambiente / `.env` |

---

## 4. Como verifico que funciona

São 96 testes em `tests/`, e todos passam. Por grupo:

**Básico:** sem decisões, nada muda e tudo fica `pending`. Aceitar uma. Rejeitar uma. Decisões
misturadas no mesmo trecho. **O resultado é o mesmo com sugestões e decisões em qualquer ordem.**
Edições que mudam o tamanho do texto (remoção, inserção, expansão). Trechos não interferem uns nos
outros. A entrada não é alterada. Sugestão que não muda nada.

**Conflitos:** sobreposição parcial, e o resto do lote é aplicado mesmo assim. Tradução do trecho
inteiro junto com revisão pontual. A tradução sozinha é aplicada. Intervalos que se encostam são
aplicados. Inserções nas bordas de um intervalo são aplicadas na ordem certa (`ab[CD]ef`). Inserção
dentro de um intervalo dá conflito. Duas inserções no mesmo ponto dão conflito. Uma sugestão
rejeitada que se sobrepõe a uma aceita não causa conflito.

**Texto desatualizado:** offset desatualizado com `original` único é realocado e aplicado.
`original` que sumiu vira `stale`. `original` ambíguo vira `stale`, inclusive com ocorrências que
se sobrepõem (`"aa"` em `"aaa"`). Offsets além do fim de um trecho que encolheu. Inserção com
offset fora do trecho. Reenviar uma sugestão já aplicada dá `stale`.

**Rebase:** a pendente depois de uma edição aplicada é deslocada e, aceita na rodada seguinte,
produz o texto correto. A pendente antes da edição não se mexe. A pendente sobreposta vira
`superseded`. A pendente realocada volta com offsets novos.
**Teste de propriedade (50 seeds):** gera texto e sugestões aleatórias sem sobreposição, e verifica
que aceitar tudo num lote dá o mesmo texto que aceitar em rodadas aleatórias usando
`remaining_suggestions`. É esse teste que dá confiança no rebase como um todo, e não só nos casos
que eu pensei.

**Entrada malformada:** decisão para id inexistente vai para `errors` e o resto é aplicado.
Decisões repetidas idênticas funcionam. Decisões contraditórias viram `invalid`. Trecho
inexistente, id duplicado, intervalo negativo, invertido ou com tamanho diferente de `original`
viram `invalid`. Trecho duplicado no artigo levanta `ValueError`.

**Outros:** aceitar com ajuste, incluindo `""` para apagar. Acentos e emoji (offsets em code
points). Inserção em trecho vazio. Artigo vazio.

**HTTP:** 200 com o payload esperado, 422 para payload malformado e 400 para trecho duplicado.

---

## 5. Como rodar

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
cp .env.example .env          # ajuste se precisar; o .env não é versionado

.venv/bin/pytest -q           # testes
.venv/bin/uvicorn alenna.http:app --reload   # API em http://localhost:8000/docs
```

Exemplo:

```bash
curl -X POST localhost:8000/articles/apply-suggestions \
  -H 'content-type: application/json' \
  -d '{
    "article": {"id": "a1", "segments": [{"id": "s1", "text": "O metodo funciona."}]},
    "suggestions": [{"id": "x", "segment_id": "s1", "start": 2, "end": 8,
                     "original": "metodo", "replacement": "método"}],
    "decisions": [{"suggestion_id": "x", "action": "accept"}]
  }'
```

```json
{
  "article": {"id": "a1", "segments": [{"id": "s1", "text": "O método funciona."}]},
  "results": [{"suggestion_id": "x", "outcome": "applied", "reason": null}],
  "remaining_suggestions": [],
  "errors": []
}
```

**Configuração e dados sensíveis:** toda configuração vem de variáveis de ambiente, carregadas de
`.env` por `alenna/config.py`. O `.env` está no `.gitignore`, e só o `.env.example` (sem segredos)
é versionado. Hoje não há nenhum segredo real. Uma chave de LLM ou URL de banco, quando existir,
entra como campo em `Settings`, com o valor vindo só do ambiente.

---

## 6. Limitações e próximos passos

- **Âncora mais robusta:** guardar um pouco de contexto antes e depois de cada sugestão. Isso
  resolveria a realocação de inserções puras (D10), casos com mais de uma ocorrência (D9) e o risco
  de reaplicar uma sugestão (D18).
- **Versão do trecho:** gravar na sugestão o hash ou a versão do trecho em que ela foi gerada.
  Assim, "o texto mudou" seria detectado de forma explícita, sem depender de comparar o `original`.
- **Status persistido:** com banco, guardar o status de cada sugestão e ignorar as que já estão
  `applied`. Isso fecha a questão da idempotência.
- **Sugestões que dependem umas das outras:** hoje cada sugestão é independente. Se a IA gerar uma
  sugestão que só faz sentido junto com outra, o modelo precisaria de grupos (aceitar tudo ou nada).
- **Desfazer:** como a função é pura e devolve o artigo novo, basta guardar a versão anterior.
- **Offsets UTF-16:** se o frontend mandar offsets em UTF-16, converter na borda da API.
