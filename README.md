# Dynamic Pricing Engine (DPE) — MVP 4.0

**Ценообразование на дорожном графе Москвы:** Dijkstra OD, **аддитивный surge**, switchback A/B и опциональный **fail-open** uplift через [Causal Pricing Engine (CPE)](https://github.com/BigGhut/Causal-pricing-engine).

Python ≥ 3.10 · FastAPI · Streamlit · CatBoost · H3 · Redis · Poetry / Docker  
DPE `:8000` · CPE `:8100` · UI `:8501`

| | DPE (этот репо) | CPE |
|:---|:---|:---|
| Роль | цена, граф, surge, A/B | ITE / Sleeping Dog hint |
| Связь | клиент ≤200 ms, fail-open | `POST /predict_uplift` |
| Док | [CAUSAL.md](CAUSAL.md) | [README CPE](https://github.com/BigGhut/Causal-pricing-engine) |

**Честно:** симуляция / портфолио-MVP, не city-scale prod. A/B-цифры — из симуляционного прогона. Без CPE цена всё равно считается (fail-open).

```text
search → DPE :8000 --uplift--> CPE :8100
              τ̂ < -θ → surge=0, CAUSAL_NO_SURGE (+ causal_* в ответе)
```

## Что внутри

- База по графу (Дейкстра) + additive \(\Delta_{surge}\) (вместо \(P\cdot S\)), k-Ring сглаживание  
- Switchback: **MULTIPLICATIVE** vs **ADDITIVE**  
- Симулятор, feature store, outbox, Streamlit  
- Causal override — детали env и parity в [CAUSAL.md](CAUSAL.md)

### Switchback (симуляция, не prod)

| Метрика | MULT | ADD |
|:---|:---:|:---:|
| Accept short (&lt;5 km) | 0% | 85% |
| CV доходов | 1.08 | 1.02 |
| Conversion | 50% | 51.4% |

## Запуск

```bash
poetry install && make test
make run-local          # API :8000 + Streamlit
# или: make docker-up   → :8000 и :8501
# pip: pip install -r requirements.txt && uvicorn src.api.main:app --port 8000
```

**С CPE:** в одном терминале поднять [CPE](https://github.com/BigGhut/Causal-pricing-engine) на `:8100`, DPE на `:8000` (`CAUSAL_ENGINE_URL=http://localhost:8100`).  
Проверка: `curl localhost:8100/health` · в ответе DPE поля `causal_*`.

## Структура

`src/api` · `src/models` (граф) · `src/features` (DriverHistoryStore) · `src/data` · `app/` (Streamlit) · `tests/` · `CAUSAL.md`

## Ссылки

[CAUSAL.md](CAUSAL.md) · [CPE](https://github.com/BigGhut/Causal-pricing-engine) · [CASE_STUDY](https://github.com/BigGhut/Causal-pricing-engine/blob/master/CASE_STUDY.md) · [evidence](https://github.com/BigGhut/Causal-pricing-engine/blob/master/docs/evidence/latest_proof.md)
