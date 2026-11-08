# Контракт для участника 3

Здесь описаны существующие схемы backend, а не вымышленные результаты анализа.

## Вход

Диалог: `GET /api/dialogs/{conversation_id}?dataset_group_id=...`.
Он содержит `dataset_group_id`, `conversation_id`, `language`, `channel`, `audio_url` и `transcript`.
Сообщение содержит `message_id`, `position`, `timestamp`, `speaker`, `text`.
Оценивается оператор; бот, клиент и системные события служат контекстом.

Чек-лист: `GET /api/checklists/{id}?version=...`.
Поля: `id`, `version`, `name`, `description`, `criteria`.
Критерий: `id`, `name`, `description`, `section`, `weight`, `answer_type`, `allow_na`, `na_condition`, `scale`.
Типы: `binary` — Да/Нет, `numeric` — одна из ступеней, `flag` — отдельное нарушение без баллов.
Ступень `scale` содержит числовое `value` и текстовое `description`.

Нельзя предполагать, что всегда будет восемь критериев: использовать переданную конфигурацию.
Идентификаторы критериев и сообщений возвращаются без изменения.

## Результат

`POST /api/evaluations`, Content-Type: application/json.

| Поле | Тип | Правило |
| --- | --- | --- |
| `dataset_group_id` | string | Существующий набор |
| `conversation_id` | string | Диалог этого набора |
| `checklist_id` | string | Существующий чек-лист |
| `checklist_version` | integer | Версия, использованная при анализе |
| `results` | array | Ровно один ответ на каждый критерий версии |

Каждый элемент `results`:

| Поле | Тип | Правило |
| --- | --- | --- |
| `criterion_id` | string | ID критерия |
| `value` | boolean / number / null | true/false для binary и flag; ступень для numeric; null для N/A |
| `explanation` | string | Непустое обоснование |
| `evidence_message_ids` | string[] | Непустой список существующих сообщений этого диалога |
| `na_reason` | string | Непустая причина при null; пустая строка при применимой оценке |

Для пропущенного действия перечислите ID сообщений проверенного участка, а в `explanation` объясните,
какое действие и где ожидалось. Не создавайте цитаты, отсутствующие в исходном разговоре.
Для N/A проверьте условие `na_condition`, а не только доступность `allow_na`.
Баллы, эффективные веса и общий итог не передаются: их вычисляет общий модуль backend.

Ответ 201 содержит ID оценки, снимок версии чек-листа, `original`, `final` и `appeals`.
Каждый результат содержит `total`, `results`, `sections`, `critical_errors`.
В строках `results` дополнительно находятся `criterion`, `points`, `effective_weight`.
`total = null` означает, что применимых весовых критериев нет.
В `sections` находятся вклад в общий итог (`points`) и максимум этого вклада (`max_points`).

## Запуск из интерфейса

Для встроенного модуля передайте в `create_app` синхронную функцию `evaluator(dialog, checklist)`.
Backend вызовет её при `POST /api/evaluations/run` и сохранит возвращённый список `results`.
По умолчанию функция не задана, `/api/capabilities` сообщает `evaluation_available: false`,
а запрос запуска возвращает 503. Асинхронная очередь и удалённый протокол вызова модели пока не реализованы.

## Апелляции

`POST /api/appeals`: `evaluation_id`, `criterion_id`, `comment`.

`POST /api/appeals/{id}/decision`:

- `action: confirm` и непустой `reason` подтверждают спорную оценку; `replacement` отсутствует.
- `action: change`, непустой `reason` и `replacement` меняют её.
- `replacement` имеет тот же формат элемента `results`, включая доказательства и причину N/A.

Решение проверяется по той версии чек-листа, которая использовалась при исходной оценке.
Исходный результат неизменяем. Финальный вычисляется с учётом сохранённых решений.
Одновременное повторное решение возвращает 409.
