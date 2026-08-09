# Causal Pricing Engine Integration in DPE

## Переменные окружения (Environment Variables)

| Переменная | Описание | Значение по умолчанию |
|:---|:---|:---|
| `CAUSAL_ENABLED` | Включение Causal Engine надбавок/переопределений | `true` |
| `CAUSAL_ENGINE_URL` | Базовый URL микросервиса CPE | `http://localhost:8100` |
| `CAUSAL_ENGINE_TIMEOUT_SEC` | Максимальный таймаут ожидания ответа от CPE (Fail-Open гарантия) | `0.2` (200 мс) |
| `CAUSAL_UPLIFT_THRESHOLD` | Порог ITE для срабатывания Sleeping Dog override | `0.05` |

## DriverHistoryStore (Online Feature Store)

`DriverHistoryStore` ведет учет истории поездок водителей в оперативной памяти с поддержкой первоначальной загрузки из таблицы `simulation_analytics` SQLite базы данных.

### Определение признаков:
- **`past_trips`**: Количество поездок водителя `driver_id` (или гео-ячейки `h3_cell` для холодных водителей) **строго до** текущего запроса расчёта стоимости.
- **`avg_surge`**: Накопленное среднее значение надбавки `surge_bonus` водителем (или гео-ячейки `h3_cell`) **строго до** текущего запроса.

### Sleeping Dog Override:
Если Causal Engine возвращает `uplift_score < -CAUSAL_UPLIFT_THRESHOLD`:
- Surge-надбавка `surge_bonus` сбрасывается в `0.0`.
- Группа теста устанавливается в `CAUSAL_NO_SURGE`.
- В ответ API возвращается `causal_override = True` и подробное объяснение в `explanation`.
