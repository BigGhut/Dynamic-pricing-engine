from typing import Tuple, Dict, Any, Optional
from src import config

class BusinessRulesEngine:
    @staticmethod
    def apply_rules(
        h3_cell: str,
        proposed_price: float,
        previous_price: Optional[float] = None
    ) -> Tuple[float, str]:
        """
        Проверяет предложенную цену ML-модели на соответствие бизнес-ограничениям:
        - Price Floor (нижняя граница)
        - Price Ceiling (верхняя граница)
        - Velocity Limit (скорость изменения цены к предыдущему шагу)
        
        Возвращает кортеж: (Итоговая цена, Объяснение причин корректировки)
        """
        reasons = []
        final_price = proposed_price

        # 1. Проверка Price Floor
        if final_price < config.MIN_PRICE:
            final_price = config.MIN_PRICE
            reasons.append(f"Price Floor triggered (adjusted from {proposed_price:.1f} to {config.MIN_PRICE})")

        # 2. Проверка Price Ceiling
        elif final_price > config.MAX_PRICE:
            final_price = config.MAX_PRICE
            reasons.append(f"Price Ceiling triggered (adjusted from {proposed_price:.1f} to {config.MAX_PRICE})")

        # 3. Проверка Velocity Limit (ограничение скорости изменения)
        if previous_price is not None:
            max_allowed_increase = previous_price * (1.0 + config.MAX_PRICE_CHANGE_PCT)
            min_allowed_decrease = previous_price * (1.0 - config.MAX_PRICE_CHANGE_PCT)

            if final_price > max_allowed_increase:
                reasons.append(
                    f"Velocity Limit exceeded (capped increase from previous {previous_price:.1f} "
                    f"to max allowed {max_allowed_increase:.1f}, proposed was {final_price:.1f})"
                )
                final_price = max_allowed_increase
            elif final_price < min_allowed_decrease:
                reasons.append(
                    f"Velocity Limit exceeded (capped decrease from previous {previous_price:.1f} "
                    f"to min allowed {min_allowed_decrease:.1f}, proposed was {final_price:.1f})"
                )
                final_price = min_allowed_decrease

        # 4. Формирование финального объяснения (Explainability)
        if not reasons:
            explanation = "ML proposed price is within all safety limits."
        else:
            explanation = " | ".join(reasons)

        return round(final_price, 2), explanation
