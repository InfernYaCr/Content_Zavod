# Yandex AI Studio Search API — отчёт о тестировании

Дата проверки: 2026-08-07
Каталог (folderId): `b1gce42uktlobipu6gju`
Базовый хост: `https://searchapi.api.cloud.yandex.net`
Аутентификация: заголовок `Authorization: Api-Key <API-ключ>`

> ⚠️ API-ключ был передан открытым текстом в чате при тестировании. Перед использованием в коде/CI перенести его в переменную окружения или секрет-хранилище и **не коммитить** в репозиторий. Рекомендуется ротация ключа, если он не хранится приватно.

Все 5 проверенных методов отработали успешно (HTTP 200) и вернули содержательные данные.

## 1. Web Search — `POST /v2/web/search`

Запрос:
```json
{
  "query": { "searchType": "SEARCH_TYPE_RU", "queryText": "кофемашина" },
  "folderId": "<folderId>",
  "responseFormat": "FORMAT_HTML"
}
```

Ответ: `{ "rawData": "<base64>" }` — HTML-страница выдачи Яндекса (декодированная ≈2 МБ), содержит реальные результаты поиска. Есть также `FORMAT_XML`.

Декодирование: `base64 --decode` (или `base64.b64decode` в Python) поля `rawData`.

## 2. Wordstat GetTop — `POST /v2/wordstat/topRequests`

Запрос:
```json
{ "phrase": "кофемашина", "numPhrases": 20, "folderId": "<folderId>" }
```
Опциональные поля: `regions` (массив кодов регионов), `devices`.

Ответ: `results[]` (топ фраз с `count`), `associations[]` (связанные фразы), `totalCount`. Подтверждено реальными цифрами (топ-фраза «кофемашина» — 2 012 866/мес).

## 3. Wordstat GetDynamics — `POST /v2/wordstat/dynamics`

Запрос:
```json
{
  "phrase": "кофемашина",
  "period": "PERIOD_MONTHLY",
  "fromDate": "2026-02-01T00:00:00Z",
  "toDate": "2026-07-31T00:00:00Z",
  "folderId": "<folderId>"
}
```

**Важный нюанс**: при `PERIOD_MONTHLY`/`PERIOD_WEEKLY` поле `fromDate` обязано быть первым днём месяца/недели, иначе `400 InvalidArgument` с сообщением `The from field value should be the first day of the month`.

Ответ: `results[]` с `date`, `count`, `share` (помесячная динамика подтверждена).

## 4. Wordstat GetRegionsDistribution — `POST /v2/wordstat/regions`

Запрос:
```json
{ "phrase": "кофемашина", "region": "REGION_REGIONS", "folderId": "<folderId>" }
```
`region` также принимает `REGION_ALL`, `REGION_CITIES`.

Ответ: `results[]` с `region` (код), `count`, `share`, `affinityIndex`. Вернулось 340+ регионов.

## 5. Wordstat GetRegionsTree — `POST /v2/wordstat/getRegionsTree`

Запрос: `{ "folderId": "<folderId>" }`

Ответ: `regions[]` — вложенное дерево `{ id, label, children[] }`. Получено 8 узлов верхнего уровня, 1103 узла суммарно (страны/федеральные округа → регионы → города).

## Эксплуатационные замечания

- Сеть в тестовой среде периодически отдаёт SSL/connect-ошибки (`curl` exit code 35) на первой попытке к `searchapi.api.cloud.yandex.net` — это транспортная нестабильность песочницы, не проблема самого API. Рекомендуется реализовать retry (2-3 попытки) для вызовов этого хоста.
- Успешные запросы занимают до ~20-90 секунд (особенно `web/search` с большим HTML) — таймауты в интеграции стоит ставить не меньше 60-90 сек.
- Все ответы — валидный JSON, коды ошибок в формате gRPC-gateway (`code`, `message`, `details`).

## Артефакты тестов

Сырые тела запросов/ответов сохранены в scratchpad-сессии тестирования (`body.json`, `response.json`, `result.html`, `wordstat_top*.json`, `wordstat_dynamics*.json`, `wordstat_regions*.json`, `wordstat_regionstree*.json`) — не входят в репозиторий, доступны только в текущей сессии агента.
