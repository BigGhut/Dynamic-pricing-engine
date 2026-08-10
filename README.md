# Dynamic Pricing Engine (DPE) — MVP 4.0

**Одной строкой:** сервис динамического ценообразования на дорожном графе Москвы — Dijkstra OD, **аддитивный surge**, switchback A/B и опциональный **fail-open** uplift через Causal Pricing Engine (CPE).

Стек: Python ≥ 3.10 · FastAPI · Streamlit · CatBoost · H3 · Redis · SQLite · Docker / Poetry

Репозиторий: [BigGhut/Dynamic-pricing-engine](https://github.com/BigGhut/Dynamic-pricing-engine)  
Компаньон (uplift / ITE): [BigGhut/Causal-pricing-engine](https://github.com/BigGhut/Causal-pricing-engine)

---

## Что это / что это не

**Это:**

- API расчёта цены поездки (`:8000`) на графе + business rules;
- симулятор трафика и switchback A/B (MULTIPLICATIVE vs ADDITIVE);
- feature store (Redis / SQLite), outbox/CDC-воркер, Streamlit-дашборд;
- опциональная интеграция с **CPE**: Sleeping Dog override при негативном uplift.

**Это не:**

- не «боевой» city-scale прод без оговорок — стенд/симуляция и портфолио-MVP;
- цифры A/B ниже — **результаты симуляции**, не гарантированный lift на реальном городе;
- causal-override не заменяет ценовой путь: при недоступности CPE DPE остаётся **fail-open**.

---

## Компаньоны (DPE ↔ CPE)

| | **DPE** (этот репо) | **CPE** |
|:---|:---|:---|
| Порт | `:8000` | `:8100` |
| Роль | цена, граф, surge, switchback | ITE / uplift, policy hint |
| Связь | HTTP-клиент, timeout ≤ 200 ms, fail-open | `POST /predict_uplift` |
| Док | [CAUSAL.md](CAUSAL.md) | [README CPE](https://github.com/BigGhut/Causal-pricing-engine) · [CASE_STUDY](https://github.com/BigGhut/Causal-pricing-engine/blob/master/CASE_STUDY.md) · [evidence](https://github.com/BigGhut/Causal-pricing-engine/blob/master/docs/evidence/latest_proof.md) |

```text
  Rider/Driver search
          |
          v
  +------------------+     POST /predict_uplift      +------------------+
  |  DPE  :8000      |  -------------------------->  |  CPE  :8100      |
  |  graph + surge   |     timeout ≤ 200ms           |  T-Learner / DML  |
  |  switchback A/B  |     fail-open if down         |  ITE score       |
  +--------+---------+  <--------------------------  +------------------+
           |
           |  if uplift < -θ  →  surge=0, CAUSAL_NO_SURGE
           v
     price response (+ causal_* fields)
```

Env по умолчанию: `CAUSAL_ENABLED=true`, `CAUSAL_ENGINE_URL=http://localhost:8100`, `CAUSAL_ENGINE_TIMEOUT_SEC=0.2`, `CAUSAL_UPLIFT_THRESHOLD=0.05`.  
Полная таблица и train/serve parity: **[CAUSAL.md](CAUSAL.md)**.

---

## Архитектура

1. **DPE API (FastAPI)** — расчёт цены (целевой latency порядка десятков мс).  
2. **Feature Store (Redis & SQLite)** — спрос/предложение, история транзакций / симуляции.  
3. **Traffic Simulator** — имитация водителей и пассажиров (логистическая полезность поездок).  
4. **CDC / Outbox Worker** — асинхронная передача событий ценообразования.  
5. **Streamlit Dashboard** — граф и разбор A/B.  
6. **CPE (опционально)** — sidecar uplift; см. блок выше.

---

## Ключевые фичи MVP 4.0

### 1. Базовый тариф по графу (Dijkstra OD)

Вместо евклидова расстояния время \(\tau\) (сек) и дистанция \(dist\) (км) — по дорожному графу (Дейкстра):

\[
P_{base} = \max(\tau \cdot R_{sec} + dist \cdot R_{km},\; P_{min})
\]

- \(R_{sec}\) — тариф за секунду (`RATE_PER_SECOND`)  
- \(R_{km}\) — тариф за км (`RATE_PER_KILOMETER`)  
- \(P_{min}\) — минимальная цена (`MIN_FARE`)

### 2. Аддитивный surge

Чтобы снизить cherry-picking коротких заказов, вместо \(P_{base}\cdot S\) используется:

\[
\text{Price} = P_{base} + \Delta_{surge}
\]

\[
\Delta_{surge} = \alpha \cdot \tau + \beta \cdot (1 - e^{-\lambda \cdot \tau})
\]

Плюс **k-Ring сглаживание** по графу — меньше резких скачков цены на границах зон.

### 3. Switchback A/B

Сплит по виртуальным часам симуляции:

| Рука | Смысл |
|:---|:---|
| **MULTIPLICATIVE** (контроль) | классический множитель surge |
| **ADDITIVE** (тест) | аддитивный \(\Delta_{surge}\) |

### 4. Causal override (Sleeping Dog)

Если CPE вернул \(\hat\tau < -\theta\):

- `surge_bonus → 0`
- `test_group = CAUSAL_NO_SURGE`
- в ответе: `causal_override=true`, `causal_uplift_score`, `causal_recommended_treatment`

Если CPE недоступен/таймаут — обычное правило surge, запрос цены **не падает**.

---

## Результаты switchback (симуляция)

> **Оговорка:** таблица — из **симуляционного** прогона MVP, не city-scale A/B на проде.  
> Для causal-политики смотри captured evidence на стороне CPE, а не только эту таблицу.

| Метрика | Контроль (MULTIPLICATIVE) | Тест (ADDITIVE) | Интерпретация в рамках симуляции |
|:---|:---:|:---:|:---|
| Принятие коротких поездок (&lt; 5 км) | 0.0% | 85.0% | сильный сдвиг против cherry-picking коротких заказов |
| CV доходов водителей | 1.083 | 1.016 | чуть ровнее разброс заработка |
| Conversion rate | 50.0% | 51.4% | небольшой рост конверсии в прогоне |

---

## Быстрый старт

Нужен **Python ≥ 3.10**.

### Вариант 1. Poetry (рекомендуется)

```bash
poetry install
make test
make run-local          # API :8000 + Streamlit (Windows: start /B …)
# по отдельности:
# make run-api
# make run-dashboard
```

Без Make:

```bash
poetry run pytest tests/
poetry run python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000
poetry run streamlit run app/dashboard.py
```

### Вариант 2. pip + requirements

```bash
pip install -r requirements.txt
python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000
```

### Вариант 3. Docker Compose

```bash
make docker-up
# API  http://localhost:8000
# UI   http://localhost:8501
```

### Вариант 4. Streamlit Community Cloud

В дашборде заложен фоновый подъём API/симулятора — можно привязать репо к [Streamlit Community Cloud](https://share.streamlit.io/) без отдельного Docker.

### Опционально: dual-stack DPE + CPE

Локально репозитории часто лежат рядом (`../causal-pricing-engine`). На GitHub — два public-репо.

```bash
# Терминал 1 — CPE :8100
cd ../causal-pricing-engine   # или: git clone https://github.com/BigGhut/Causal-pricing-engine.git
pip install -r requirements.txt && pip install -e .
python scripts/train.py       # если ещё нет artifacts/
uvicorn src.api.main:app --host 127.0.0.1 --port 8100

# Терминал 2 — DPE :8000
cd ../dynamic-pricing-engine
# при необходимости:
# set CAUSAL_ENABLED=true
# set CAUSAL_ENGINE_URL=http://localhost:8100
poetry run python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000
```

Проверка: `GET http://localhost:8100/health` · поиск/price на DPE должен отдавать поля `causal_*` (при живом CPE — осмысленный score; при down — fail-open).

На стороне CPE: `python scripts/portfolio_proof.py --with-dpe`.

---

## Структура проекта

```text
dynamic-pricing-engine/
├── app/                    # Streamlit dashboard
├── artifacts/              # артефакты моделей / прогонов
├── src/
│   ├── api/                # FastAPI (main, schemas) :8000
│   ├── bre/                # business rules
│   ├── data/               # DB, feature store, outbox
│   ├── features/           # DriverHistoryStore (serve parity с CPE)
│   ├── models/             # demand, geogrid, road graph
│   └── config.py           # в т.ч. CAUSAL_* env
├── tests/                  # pytest (+ causal / driver history)
├── CAUSAL.md               # интеграция с CPE
├── Dockerfile / docker-compose.yml
├── Makefile
├── pyproject.toml          # Poetry
└── requirements.txt        # pip-альтернатива
```

---

## Что улучшил бы дальше

1. Крупнее switchback-логи и CUPED-readout политики causal-override (вместе с CPE).  
2. Явный контракт feature schema DPE↔CPE (shared package / OpenAPI).  
3. Более честная публикация A/B: n, seed, воспроизводимая команда прогона в CI.

---

## Ссылки

- Интеграция causal: [CAUSAL.md](CAUSAL.md)  
- CPE: [Causal-pricing-engine](https://github.com/BigGhut/Causal-pricing-engine)  
- CPE case study: [CASE_STUDY.md](https://github.com/BigGhut/Causal-pricing-engine/blob/master/CASE_STUDY.md)  
- CPE evidence: [latest_proof.md](https://github.com/BigGhut/Causal-pricing-engine/blob/master/docs/evidence/latest_proof.md)
