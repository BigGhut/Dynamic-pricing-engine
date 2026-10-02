# Интеграция Causal Pricing Engine (CPE)

CPE оценивает **один** эффект: как **аддитивная надбавка** меняет вероятность того, что **водитель примет** заказ, по сравнению с **базовым тарифом**.

Это не скидка и не контраст switchback «аддитивная формула против мультипликативной». Рука switchback назначается чётностью виртуального часа, а не водителю. На этих логах индивидуальный эффект не идентифицируется, и CPE на них не обучается.

Репозиторий: [BigGhut/Causal-pricing-engine](https://github.com/BigGhut/Causal-pricing-engine)

Вызов `POST {CAUSAL_ENGINE_URL}/predict_uplift` не роняет котировку: таймаут и ошибка — fail-open.

## Когда DPE спрашивает CPE

Только если виртуальный час **аддитивный** и в поиске есть `driver_id`. Мультипликативный час — другая цена, модель про неё не обучена. Поиск без водителя некого скорить: в симуляторе водитель выбирается уже после цены, и этот вызов водителя не передаёт.

Тело запроса:

```json
{
  "driver_id": "driver_008",
  "features": {
    "distance_km": 7.0,
    "duration_sec": 900.0,
    "hour_of_day": 11.0,
    "past_trips": 4.0,
    "avg_surge": 12.0
  }
}
```

`hour_of_day` здесь — виртуальный час, тот же, по которому выбрана рука. `price` и `surge_bonus` не отправляются: они уже зависят от руки.

`past_trips` и `avg_surge` считаются по поездкам этого водителя строго до текущего запроса (`DriverHistoryStore`).

## Что делает отрицательный score

`uplift_score` — изменение вероятности принятия от надбавки. Если он ниже `-CAUSAL_UPLIFT_THRESHOLD` (по умолчанию `0.05`):

- цена становится базовым тарифом, `surge_bonus = 0`;
- `test_group = CAUSAL_NO_SURGE`;
- в ответе `causal_override = true`, плюс `causal_uplift_score` и `causal_recommended_treatment`.

Метки CPE: `SURCHARGE`, `KEEP_QUOTE`, `NO_SURCHARGE`. Действие DPE завязано на числовой порог, не на строку скидки.

## Лог `simulation_analytics`

Колонки текущей схемы: `timestamp`, `test_group`, `trip_id`, `distance_km`, `duration_sec`, `price`, `surge_bonus`, `accepted`, `driver_utility`, `driver_id`. Новые прогоны пишут ещё `virtual_hour`.

`accepted` — водитель взял заказ. `driver_id` — этот водитель. `driver_utility` — заложенная вероятность принятия, не выручка. `timestamp` — часы процесса, не виртуальный час. Старые файлы без `virtual_hour` не получают час из `timestamp`.

## Переменные

| Переменная | По умолчанию |
|:---|:---|
| `CAUSAL_ENABLED` | `true` |
| `CAUSAL_ENGINE_URL` | `http://localhost:8100` |
| `CAUSAL_ENGINE_TIMEOUT_SEC` | `0.2` |
| `CAUSAL_UPLIFT_THRESHOLD` | `0.05` |

Seeded switchback (`python -m src.eval.switchback`) держит `CAUSAL_ENABLED=false`. Его цифры — про две формулы surge, не про этот override.
