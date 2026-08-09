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

## Training Features Caveats (CATE & Feature Entanglement)

При обучении каузальных моделей на логах симуляции DPE необходимо учитывать особенности структуры признаков:

1. **`surge_bonus` на контроле**: В случае `test_group = MULTIPLICATIVE` надбавка `surge_bonus` тождественно равна `0.0` по построению.
2. **Post-treatment признака**: Поля `price` и `surge_bonus` частично рассчитываются после назначения арма (post-treatment / arm-linked). Для строгого определения CATE и исключения эндогенности рекомендуется использовать набор признаков **`pre_treatment`** (без `price` и `surge_bonus`).
3. **Train/Serve alignment vs Causal Purity**: В online-режиме DPE передает полный набор признаков (режим `serve_parity`), сохраняя контракт сервиса. Для offline-оценки и каузального анализа поддерживается параметр `feature_mode="pre_treatment"`.

