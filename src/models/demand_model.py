import os

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor

from src import config


class DemandElasticityModel:
    def __init__(self):
        self.model = None
        self.model_path = os.path.join(config.BASE_DIR, "catboost_demand_model.bin")
        self.is_trained = False

    def generate_synthetic_data(self, num_samples: int = 2000) -> pd.DataFrame:
        """
        Генерирует синтетические исторические данные о транзакциях 
        для первоначального обучения модели (Cold Start).
        """
        np.random.seed(42)
        
        # 1. Случайные признаки
        prices = np.random.uniform(config.MIN_PRICE, config.MIN_PRICE * 4.0, num_samples)
        ds_ratios = np.random.uniform(0.1, 5.0, num_samples) # demand_supply_ratio
        competitor_prices = prices * np.random.uniform(0.8, 1.2, num_samples)
        hour = np.random.randint(0, 24, num_samples)
        
        # 2. Моделируем вероятность покупки (конверсию) по формуле эластичности
        # Высокая цена снижает конверсию, высокий спрос (ds_ratio) и высокая цена конкурента — увеличивают.
        base_conversion = 0.8
        price_penalty = -0.0015 * (prices - config.BASE_PRICE)
        ds_bonus = 0.15 * np.log1p(ds_ratios)
        comp_bonus = 0.0008 * (competitor_prices - prices)
        
        conversion_prob = base_conversion + price_penalty + ds_bonus + comp_bonus
        # Ограничиваем вероятность диапазоном [0.02, 0.98] + добавляем шум
        conversion_prob = np.clip(conversion_prob, 0.02, 0.98)
        
        # Конверсия (купил ли пользователь поездку/товар по этой цене)
        conversion = np.random.binomial(1, conversion_prob)
        
        df = pd.DataFrame({
            "price": prices,
            "demand_supply_ratio": ds_ratios,
            "competitor_price": competitor_prices,
            "hour": hour,
            "conversion": conversion
        })
        return df

    def fit_dataframe(self, df: pd.DataFrame, iterations: int = 100) -> None:
        """Fit on a frame. Does not write the model file."""
        x = df[["price", "demand_supply_ratio", "competitor_price", "hour"]]
        y = df["conversion"]
        self.model = CatBoostRegressor(
            iterations=iterations,
            learning_rate=0.1,
            depth=5,
            verbose=0,
            loss_function="RMSE",
        )
        self.model.fit(x, y)
        self.is_trained = True

    def train(self):
        """Обучает модель CatBoost на синтетической конверсии и сохраняет файл.

        Этот sandbox не участвует в котировке. Цена считается формулой в src/pricing.py.
        """
        print("[Demand Model] Генерация исторических данных для обучения...")
        df = self.generate_synthetic_data()
        print("[Demand Model] Обучение CatBoostRegressor на CPU...")
        self.fit_dataframe(df)
        self.model.save_model(self.model_path)
        print(f"[Demand Model] Модель успешно обучена и сохранена по адресу: {self.model_path}")

    def load_or_train(self):
        """Загружает модель с диска или обучает новую, если файл отсутствует."""
        if os.path.exists(self.model_path):
            try:
                self.model = CatBoostRegressor()
                self.model.load_model(self.model_path)
                self.is_trained = True
                print("[Demand Model] Обученная модель загружена с диска.")
                return
            except Exception as e:
                print(f"[Demand Model] Ошибка загрузки модели: {e}. Переходим к переобучению.")
        
        self.train()

    def predict_conversion(self, price: float, ds_ratio: float, competitor_price: float, hour: int) -> float:
        """Предсказывает вероятность покупки (конверсию) для заданных параметров."""
        if not self.is_trained or self.model is None:
            # Детерминированный фолбек (на случай сбоя модели)
            # Базовая логика эластичности спроса
            price_factor = max(0.01, 1.0 - 0.001 * (price - config.BASE_PRICE))
            ds_factor = min(1.5, 0.8 + 0.1 * ds_ratio)
            return float(np.clip(price_factor * ds_factor, 0.05, 0.95))
            
        X = pd.DataFrame([{
            "price": price,
            "demand_supply_ratio": ds_ratio,
            "competitor_price": competitor_price,
            "hour": hour
        }])
        
        # Получаем предсказание и клипаем в пределы вероятности
        pred = self.model.predict(X)[0]
        return float(np.clip(pred, 0.01, 0.99))

    def find_optimal_price(self, ds_ratio: float, competitor_price: float, hour: int) -> float:
        """
        Численно находит оптимальную цену в диапазоне [MIN_PRICE, MAX_PRICE] с шагом 5 рублей,
        которая максимизирует математическое ожидание выручки:
        Expected Revenue = Price * P(Conversion | Price)
        """
        # Сетка цен
        price_grid = np.arange(config.MIN_PRICE, config.MIN_PRICE * 3.5, 5.0)
        
        best_price = config.BASE_PRICE
        max_expected_revenue = 0.0
        
        for price in price_grid:
            prob = self.predict_conversion(price, ds_ratio, competitor_price, hour)
            expected_revenue = price * prob
            
            if expected_revenue > max_expected_revenue:
                max_expected_revenue = expected_revenue
                best_price = price
                
        return float(best_price)
