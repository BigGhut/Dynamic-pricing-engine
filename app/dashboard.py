import time
import sqlite3
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
import requests
import altair as alt
from src import config
import json

# Настройка страницы
st.set_page_config(
    page_title="Dynamic Pricing Engine (DPE) Dashboard",
    page_icon="🚘",
    layout="wide"
)

# Автозапуск API-сервера и симулятора в монолитном фоновом режиме (для оффлайн и Streamlit Cloud)
api_running = False
try:
    resp = requests.get("http://127.0.0.1:8000/health", timeout=0.15)
    if resp.status_code == 200:
        api_running = True
except Exception:
    pass

if not api_running:
    import threading
    import uvicorn
    from src.api.main import app as fastapi_app
    from run_simulation import SimulationRunner

    # 1. Запуск DPE API
    def run_api_server():
        try:
            uvicorn.run(fastapi_app, host="127.0.0.1", port=8000, log_level="warning")
        except Exception as e:
            print(f"[Monolith API] Error: {e}")

    api_thread = threading.Thread(target=run_api_server, daemon=True)
    api_thread.start()

    # 2. Запуск симулятора трафика
    def run_simulator_loop():
        time.sleep(3.0)  # Даем время API-серверу на запуск
        try:
            simulator = SimulationRunner()
            while True:
                simulator.run_tick()
                time.sleep(4.0)
        except Exception as e:
            print(f"[Monolith Simulator] Error: {e}")

    sim_thread = threading.Thread(target=run_simulator_loop, daemon=True)
    sim_thread.start()
    
    st.toast("🚀 Сервис DPE и Симулятор автоматически запущены в фоне!")

# Функция подключения к SQLite для логов ценообразования
def get_data_from_db():
    conn = sqlite3.connect(config.DB_PATH)
    df_prices = pd.read_sql_query(
        "SELECT * FROM prices ORDER BY created_at DESC LIMIT 200", 
        conn
    )
    df_outbox = pd.read_sql_query(
        "SELECT status, COUNT(*) as count FROM price_outbox GROUP BY status", 
        conn
    )
    conn.close()
    return df_prices, df_outbox

# Функция загрузки аналитики принятия заказов
def get_analytics_data():
    conn = sqlite3.connect(config.DB_PATH)
    try:
        df = pd.read_sql_query("SELECT * FROM simulation_analytics", conn)
    except Exception:
        df = pd.DataFrame()
    conn.close()
    return df

st.title("Платформа динамического ценообразования (Dynamic Pricing Engine / DPE) MVP 4.0")
st.markdown(
    "MVP-4.0 с поддержкой **Аддитивного механизма компенсации (Additive Surge)**, "
    "внедрением алгоритма Dijkstra для OD-маршрутов и аналитикой Switchback-тестирования в реальном времени."
)

# Чекбокс для автообновления страницы
auto_refresh = st.sidebar.checkbox("Автоматическое обновление (каждые 3 сек)", value=True)

# Загружаем данные
try:
    df_prices, df_outbox = get_data_from_db()
    df_analytics = get_analytics_data()
except Exception as e:
    st.warning("База данных еще не инициализирована. Запустите API сервер и симулятор, чтобы наполнить данными!")
    df_prices, df_outbox, df_analytics = pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

# Пытаемся получить состояние графа с API сервера
graph_state = None
try:
    resp = requests.get("http://localhost:8000/api/v1/graph/state", timeout=1.0)
    if resp.status_code == 200:
        graph_state = resp.json()
except Exception:
    pass

# Определяем текущую активную группу Switchback-теста
current_group = "Неизвестно"
if not df_prices.empty:
    current_group = df_prices.iloc[0]["test_group"]

# Показываем статус Switchback теста в боковой панели
st.sidebar.markdown("---")
st.sidebar.subheader("Параметры A/B эксперимента")
st.sidebar.info(f"Текущая активная группа:\n**{current_group}**")
st.sidebar.write("Смена группы происходит каждый час.")

if not df_prices.empty:
    # --- Секция 1. Ключевые метрики ---
    col1, col2, col3, col4 = st.columns(4)
    
    # Средний сурдж
    avg_surge = df_prices["surge_multiplier"].mean()
    col1.metric("Средний Surge-множитель", f"{avg_surge:.2f}x")
    
    # Всего расчетов цен
    total_calculations = len(df_prices)
    col2.metric("Всего расчетов за сессию", f"{total_calculations}")
    
    # Максимальный аддитивный бонус
    max_bonus = df_prices["surge_bonus"].max()
    col3.metric("Максимальный бонус (Additive Surge)", f"{max_bonus:.1f} руб.")
    
    # Количество Fail-Static инцидентов
    fail_static_count = df_prices["explanation"].str.contains("Fail-Static").sum()
    col4.metric("Аварийных откатов (Fail-Static)", f"{fail_static_count}")

    # --- Секция 2. Результаты Switchback A/B эксперимента ---
    st.subheader("Аналитика Switchback A/B тестирования (Additive vs Multiplicative)")
    
    if not df_analytics.empty:
        tab1, tab2, tab3 = st.tabs([
            "Уровень принятия заказов (Acceptance Rate)", 
            "Вариативность доходов водителей", 
            "Пропускная способность системы (Throughput)"
        ])
        
        with tab1:
            st.markdown(
                "**Метрика Cherry-picking (Умышленная селекция):** В мультипликативной модели водители часто отклоняют "
                "короткие поездки, надеясь получить длинный заказ с большим множителем. В аддитивной модели "
                "полезность коротких поездок выравнивается, уменьшая процент отказов."
            )
            
            # Разделим поездки по длине: Короткие (< 5 км) и Длинные (>= 10 км)
            df_analytics["trip_type"] = pd.cut(
                df_analytics["distance_km"], 
                bins=[0, 5, 10, 100], 
                labels=["Короткие (<5 км)", "Средние (5-10 км)", "Длинные (>=10 км)"]
            )
            
            # Группируем по группе теста и типу поездки для вычисления Acceptance Rate
            ar_stats = df_analytics.groupby(["test_group", "trip_type"], observed=False)["accepted"].agg(["count", "mean"]).reset_index()
            ar_stats["Acceptance Rate (%)"] = round(ar_stats["mean"] * 100, 1)
            
            # Переформатируем для отображения в Streamlit
            ar_pivot = ar_stats.pivot(index="trip_type", columns="test_group", values="Acceptance Rate (%)")
            st.table(ar_pivot)
            
            # Красивый группированный бок-о-бок график Altair (устраняет Stacked Percentage Bug)
            chart = alt.Chart(ar_stats).mark_bar().encode(
                x=alt.X('test_group:N', title='Группа теста', axis=alt.Axis(labels=True)),
                y=alt.Y('Acceptance Rate (%):Q', title='Acceptance Rate (%)', scale=alt.Scale(domain=[0, 100])),
                color=alt.Color('test_group:N', legend=alt.Legend(title="Группа теста")),
                column=alt.Column('trip_type:N', title='Категория поездки')
            ).properties(width=180, height=280)
            st.altair_chart(chart, use_container_width=False)

        with tab2:
            st.markdown(
                "**Дисперсия доходов водителей (Driver Earnings Variance):** Аддитивный Surge сглаживает относительный разброс "
                "доходов водителей. Для корректной оценки используется относительная метрика — "
                "**Коэффициент вариации (Coefficient of Variation, $CV = \\sigma / \\mu$)**, нивелирующая разницу в масштабах доходов."
            )
            
            # Рассчитаем доход водителя за каждую принятую поездку
            # Чистый доход = Цена * 0.8 (после комиссии) - Дистанция * 6.0 (амортизация и бензин)
            df_analytics["driver_earnings"] = df_analytics.apply(
                lambda row: (row["price"] * 0.8 - row["distance_km"] * 6.0) if row["accepted"] == 1 else 0.0,
                axis=1
            )
            
            # Группируем заработок по водителям и тестовой группе
            driver_earning_sum = df_analytics.groupby(["test_group", "driver_id"])["driver_earnings"].sum().reset_index()
            
            # Считаем среднее, стандартное отклонение и CV
            stats_df = driver_earning_sum.groupby("test_group")["driver_earnings"].agg(["mean", "std"]).reset_index()
            stats_df["cv"] = stats_df["std"] / stats_df["mean"]
            
            stats_df.columns = [
                "Группа теста", 
                "Средний доход водителя (руб.)", 
                "Стандартное отклонение (\u03c3, руб.)", 
                "Коэффициент вариации (CV)"
            ]
            
            # Округляем для красоты
            stats_df["Средний доход водителя (руб.)"] = stats_df["Средний доход водителя (руб.)"].round(1)
            stats_df["Стандартное отклонение (\u03c3, руб.)"] = stats_df["Стандартное отклонение (\u03c3, руб.)"].round(1)
            stats_df["Коэффициент вариации (CV)"] = stats_df["Коэффициент вариации (CV)"].round(3)
            
            st.dataframe(stats_df, hide_index=True)
            
            # Отрисуем график CV
            cv_chart = alt.Chart(stats_df).mark_bar(size=40).encode(
                x=alt.X("Группа теста:N", title="Группа теста"),
                y=alt.Y("Коэффициент вариации (CV):Q", title="Коэффициент вариации (CV)"),
                color="Группа теста:N"
            ).properties(width=300, height=250)
            st.altair_chart(cv_chart, use_container_width=False)
            
        with tab3:
            st.markdown(
                "**Trip Throughput:** Количество выполненных (принятых) заказов в единицу времени. "
                "Снижение отказов в аддитивной группе должно приводить к росту пропускной способности."
            )
            
            # Подсчет общего количества заказов и принятых заказов
            throughput = df_analytics.groupby("test_group").agg(
                total_requests=("trip_id", "count"),
                accepted_trips=("accepted", "sum")
            ).reset_index()
            
            throughput["Conversion Rate (%)"] = round(
                (throughput["accepted_trips"] / throughput["total_requests"]) * 100, 1
            )
            st.dataframe(throughput, hide_index=True)
    else:
        st.info("Ожидание накопления достаточного количества данных симуляции для сравнения A/B групп...")

    # --- Секция 3. Интерактивная карта дорожного графа ---
    st.subheader("Интерактивная карта дорожного графа Москвы")
    
    if True:  # Переходим на полностью статический HTML-код iframe с фоновым fetch-опросом API
        yandex_map_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                html, body {{
                    width: 100%;
                    height: 100%;
                    margin: 0;
                    padding: 0;
                    background: #111;
                    font-family: sans-serif;
                }}
                #map {{
                    width: 100%;
                    height: 100%;
                    border-radius: 8px;
                }}
                .dark-theme {{
                    filter: invert(90%) hue-rotate(180deg) brightness(95%) contrast(90%);
                }}
            </style>
            <!-- Подключаем API Яндекс Карт напрямую в head, как в первой версии. 
                 Поскольку iframe статический и не перезагружается, скрипт загрузится только один раз. -->
            <script src="https://api-maps.yandex.ru/2.1/?lang=ru_RU" type="text/javascript"></script>
        </head>
        <body>
            <div id="map"></div>
            <script type="text/javascript">
                var myMap = null;
                var isOffline = false;

                function drawLocalGraphCanvas(nodesList, edgesList) {{
                    var mapDiv = document.getElementById('map');
                    mapDiv.innerHTML = `
                        <div style="position: relative; width: 100%; height: 100%; background: #151515; border-radius: 8px; border: 1px solid #333; box-sizing: border-box; overflow: hidden;">
                            <div style="position: absolute; top: 10px; left: 10px; z-index: 10; color: #fff; background: rgba(0,0,0,0.75); padding: 5px 10px; border-radius: 4px; font-size: 11px; pointer-events: none; border: 1px solid #444; font-family: sans-serif;">
                                ⚠️ Сбой API Яндекс.Карт | Автономный режим
                            </div>
                            <canvas id="graphCanvas" style="width: 100%; height: 100%; display: block;"></canvas>
                        </div>
                    `;
                    
                    var canvas = document.getElementById('graphCanvas');
                    if (!canvas) return;
                    var ctx = canvas.getContext('2d');
                    
                    canvas.width = mapDiv.clientWidth || 800;
                    canvas.height = mapDiv.clientHeight || 600;
                    
                    ctx.clearRect(0, 0, canvas.width, canvas.height);
                    
                    if (nodesList.length === 0) return;
                    
                    var minLat = 90, maxLat = -90, minLon = 180, maxLon = -180;
                    nodesList.forEach(function(n) {{
                        var lat = n.coords[0];
                        var lon = n.coords[1];
                        if (lat < minLat) minLat = lat;
                        if (lat > maxLat) maxLat = lat;
                        if (lon < minLon) minLon = lon;
                        if (lon > maxLon) maxLon = lon;
                    }});
                    
                    var latSpan = maxLat - minLat || 0.01;
                    var lonSpan = maxLon - minLon || 0.01;
                    
                    function toCanvasCoords(lat, lon) {{
                        var padding = 45;
                        var x = padding + (lon - minLon) / lonSpan * (canvas.width - 2 * padding);
                        var y = canvas.height - (padding + (lat - minLat) / latSpan * (canvas.height - 2 * padding));
                        return {{ x: x, y: y }};
                    }}
                    
                    edgesList.forEach(function(edge) {{
                        var p1 = toCanvasCoords(edge.geometry[0][0], edge.geometry[0][1]);
                        var p2 = toCanvasCoords(edge.geometry[1][0], edge.geometry[1][1]);
                        
                        ctx.beginPath();
                        ctx.moveTo(p1.x, p1.y);
                        ctx.lineTo(p2.x, p2.y);
                        ctx.strokeStyle = edge.color;
                        ctx.lineWidth = edge.width * 0.7;
                        ctx.globalAlpha = 0.55;
                        ctx.stroke();
                    }});
                    
                    nodesList.forEach(function(node) {{
                        var p = toCanvasCoords(node.coords[0], node.coords[1]);
                        
                        ctx.beginPath();
                        ctx.arc(p.x, p.y, 8, 0, 2 * Math.PI);
                        ctx.fillStyle = node.color;
                        ctx.globalAlpha = 0.85;
                        ctx.fill();
                        ctx.strokeStyle = '#ffffff';
                        ctx.lineWidth = 1.2;
                        ctx.stroke();
                        
                        ctx.fillStyle = '#ffffff';
                        ctx.font = '10px sans-serif';
                        ctx.globalAlpha = 1.0;
                        ctx.fillText(node.hint, p.x + 10, p.y + 3);
                    }});
                }}

                function drawYandex(nodesList, edgesList) {{
                    if (!myMap) return;
                    myMap.geoObjects.removeAll();
                    
                    edgesList.forEach(function(edge) {{
                        var polyline = new ymaps.Polyline(
                            edge.geometry, 
                            {{ hintContent: edge.hint }}, 
                            {{ strokeColor: edge.color, strokeWidth: edge.width, strokeOpacity: 0.6 }}
                        );
                        myMap.geoObjects.add(polyline);
                    }});
                    
                    nodesList.forEach(function(node) {{
                        var circle = new ymaps.Circle(
                            [node.coords, 350],
                            {{ 
                                hintContent: node.hint,
                                balloonContent: "<b>Узел графа:</b> " + node.name + "<br/>" +
                                                 "<b>Текущий Surge:</b> " + node.surge + "x<br/>" +
                                                 "<b>Бонус:</b> +" + Math.round(node.bonus) + " руб.<br/>" +
                                                 "<b>Рекомендованная цена:</b> " + node.price + " руб."
                            }}, 
                            {{
                                fillColor: node.color,
                                strokeColor: "#ffffff",
                                fillOpacity: 0.75,
                                strokeWidth: 1.5
                            }}
                        );
                        myMap.geoObjects.add(circle);
                    }});
                }}

                function updateData() {{
                    fetch('http://127.0.0.1:8000/api/v1/graph/state')
                        .then(function(response) {{
                            return response.json();
                        }})
                        .then(function(data) {{
                            var mapNodes = [];
                            for (var nodeId in data.nodes) {{
                                var nInfo = data.nodes[nodeId];
                                var lat = nInfo.lat;
                                var lon = nInfo.lon;
                                var h3Cell = nInfo.h3_cell;
                                
                                var price = {config.BASE_PRICE};
                                var surge = 1.0;
                                var bonus = 0.0;
                                
                                if (data.latest_prices && data.latest_prices[h3Cell]) {{
                                    var pData = data.latest_prices[h3Cell];
                                    price = pData.price;
                                    surge = pData.surge_multiplier;
                                    bonus = pData.surge_bonus;
                                }}
                                
                                var color = "#2ca02c";
                                if (surge > 1.4) color = "#d62728";
                                else if (surge > 1.05) color = "#ff7f0e";
                                
                                mapNodes.push({{
                                    id: nodeId,
                                    coords: [lat, lon],
                                    name: nInfo.name,
                                    surge: surge,
                                    bonus: bonus,
                                    price: price,
                                    color: color,
                                    hint: nInfo.name + ": " + surge + "x"
                                }});
                            }}
                            
                            var mapEdges = [];
                            for (var edgeKey in data.edge_speeds) {{
                                var speed = data.edge_speeds[edgeKey];
                                var parts = edgeKey.split("->");
                                var u = parts[0];
                                var v = parts[1];
                                
                                var uInfo = data.nodes[u];
                                var vInfo = data.nodes[v];
                                if (!uInfo || !vInfo) continue;
                                
                                var metadata = data.edge_metadata[edgeKey] || {{ base_speed: 60, road_type: "Secondary", distance_km: 1.0 }};
                                var baseSpeed = metadata.base_speed;
                                var ratio = speed / baseSpeed;
                                
                                var color = "#2ca02c";
                                var width = 3.0;
                                if (ratio < 0.4) {{
                                    color = "#d62728";
                                    width = 5.5;
                                }} else if (ratio < 0.8) {{
                                    color = "#ff7f0e";
                                    width = 4.0;
                                }}
                                
                                mapEdges.push({{
                                    geometry: [
                                        [uInfo.lat, uInfo.lon],
                                        [vInfo.lat, vInfo.lon]
                                    ],
                                    color: color,
                                    width: width,
                                    hint: "Скорость: " + Math.round(speed) + " км/ч"
                                }});
                            }}
                            
                            if (isOffline || typeof ymaps === 'undefined' || !myMap) {{
                                drawLocalGraphCanvas(mapNodes, mapEdges);
                            }} else {{
                                drawYandex(mapNodes, mapEdges);
                            }}
                        }})
                        .catch(function(err) {{
                            console.error("Error updating graph data: ", err);
                        }});
                }}

                // Проверяем наличие библиотеки Яндекс.Карт
                if (typeof ymaps === 'undefined') {{
                    console.warn("Yandex Maps API is not available. Falling back to offline canvas render.");
                    isOffline = true;
                    drawLocalGraphCanvas([], []);
                    updateData();
                    setInterval(updateData, 2500);
                }} else {{
                    document.getElementById('map').classList.add('dark-theme');
                    ymaps.ready(init);
                }}

                function init() {{
                    try {{
                        myMap = new ymaps.Map("map", {{
                            center: [{config.SIM_TOWN_CENTER_LAT}, {config.SIM_TOWN_CENTER_LON}],
                            zoom: 11,
                            controls: ['zoomControl']
                        }});
                        
                        updateData();
                        setInterval(updateData, 2500);
                    }} catch (err) {{
                        console.error("Yandex Maps initialization error: ", err);
                        isOffline = true;
                        drawLocalGraphCanvas([], []);
                        updateData();
                        setInterval(updateData, 2500);
                    }}
                }}
            </script>
        </body>
        </html>
        """
        components.html(yandex_map_html, height=600)
    else:
        st.info("Ожидание запуска API-сервера...")

    # --- Секция 4. Лог ценообразования и Outbox ---
    st.subheader("Последние события расчета цен (Decision audit)")
    st.dataframe(
        df_prices[[
            "test_group", "payout_formula", "h3_index", "price", "base_price", 
            "surge_bonus", "surge_multiplier", "explanation", "created_at"
        ]]
        .rename(columns={
            "test_group": "Группа Switchback",
            "payout_formula": "Формула расчета",
            "h3_index": "H3 Ячейка",
            "price": "Итоговая цена (RUB)",
            "base_price": "Базовый тариф (RUB)",
            "surge_bonus": "Сурдж-бонус (RUB)",
            "surge_multiplier": "Множитель Surge",
            "explanation": "Объяснение алгоритма",
            "created_at": "Время расчета"
        }),
        width="stretch"
    )

    st.subheader("Статус CDC / Transactional Outbox")
    if not df_outbox.empty:
        st.write(df_outbox)
    else:
        st.info("Нет событий в очереди outbox.")

else:
    st.info("Пока нет данных о ценах. Запустите API сервер и симулятор!")

# Автообновление
if auto_refresh:
    time.sleep(3)
    st.rerun()
