# Интеграция Causal Pricing Engine (CPE) в DPE

**CPE** — sidecar uplift / ITE на порту `:8100`.  
Репозиторий: [BigGhut/Causal-pricing-engine](https://github.com/BigGhut/Causal-pricing-engine)  
Case study: [CASE_STUDY.md](https://github.com/BigGhut/Causal-pricing-engine/blob/master/CASE_STUDY.md)  
Captured evidence: [docs/evidence/latest_proof.md](https://github.com/BigGhut/Causal-pricing-engine/blob/master/docs/evidence/latest_proof.md)

DPE вызывает `POST {CAUSAL_ENGINE_URL}/predict_uplift` при расчёте цены. При ошибке или таймауте ценовой путь **не падает** (fail-open).

---

## Переменные окружения

| Переменная | Описание | По умолчанию |
|:---|:---|:---|
| `CAUSAL_ENABLED` | Включение causal-override | `true` |
| `CAUSAL_ENGINE_URL` | Базовый URL CPE | `http://localhost:8100` |
| `CAUSAL_ENGINE_TIMEOUT_SEC` | Таймаут HTTP к CPE (fail-open) | `0.2` (200 ms) |
| `CAUSAL_UPLIFT_THRESHOLD` | Порог ITE для Sleeping Dog | `0.05` |

---

## DriverHistoryStore (online feature store)

`DriverHistoryStore` ведёт историю поездок в памяти с возможностью начальной загрузки из `simulation_analytics` (SQLite).

### Признаки

- **`past_trips`** — число поездок `driver_id` (или geo-ячейки `h3_cell` для cold-start) **строго до** текущего запроса цены.  
- **`avg_surge`** — накопленное среднее `surge_bonus` **строго до** текущего запроса.

Так online-признаки совпадают по определению с offline-пайплайном CPE (`load_dpe_data`) — train/serve parity.

---

## Sleeping Dog override

Если CPE вернул `uplift_score < -CAUSAL_UPLIFT_THRESHOLD`:

- `surge_bonus` сбрасывается в `0.0`;
- группа теста: `CAUSAL_NO_SURGE`;
- в ответе API: `causal_override = true`, плюс пояснение в `explanation`;
- также отдаются `causal_uplift_score`, `causal_recommended_treatment`.

---

## Caveats для обучения CATE (feature entanglement)

При обучении каузальных моделей на логах симуляции DPE:

1. **`surge_bonus` на контроле:** при `test_group = MULTIPLICATIVE` надбавка `surge_bonus` тождественно `0.0` по построению.  
2. **Post-treatment признаки:** `price` и `surge_bonus` частично зависят от арма (arm-linked). Для более чистого CATE offline используйте набор **`pre_treatment`** (без `price` / `surge_bonus`).  
3. **Train/serve vs causal purity:** online DPE шлёт полный serve-набор (`serve_parity`). Offline CPE поддерживает `feature_mode="pre_treatment"` для каузального анализа.

---

## Быстрая проверка

```bash
# CPE жив
curl -s http://localhost:8100/health

# DPE search/price (поля causal_* в JSON-ответе)
# см. src/api/schemas.py — causal_uplift_score, causal_override, causal_recommended_treatment
```

На стороне CPE end-to-end proof против live DPE:

```bash
cd ../causal-pricing-engine
python scripts/portfolio_proof.py --with-dpe
```
