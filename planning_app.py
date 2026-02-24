import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# 1. Настройка страницы
st.set_page_config(page_title="Quarterly Planning", layout="wide")

# --- 2. ПОДКЛЮЧЕНИЕ К GOOGLE SHEETS ---
def get_client():
    try:
        if "gcp_service_account" not in st.secrets:
            st.error("❌ Не найден раздел [gcp_service_account] в Secrets.")
            st.stop()
            
        creds_dict = dict(st.secrets["gcp_service_account"])
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        st.error(f"❌ Ошибка подключения: {e}")
        st.stop()

def get_main_sheet():
    client = get_client()
    return client.open("Quarterly Planning Data").sheet1

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ RICE ---
def make_text_bar(val, max_val):
    """Создает красивый текстовый прогресс-бар для ячеек Гугл Таблицы"""
    try:
        val = int(val)
    except:
        val = 0
    filled = "█" * val
    empty = "░" * (max_val - val)
    return f"{filled} {val}/{max_val}"

def calculate_rice(reach, impact, confidence_str, sp):
    """Считает RICE: (Reach * Impact * Confidence) / Effort"""
    conf_map = {"100% (Уверен)": 1.0, "80% (Скорее уверен)": 0.8, "50% (Интуиция)": 0.5}
    conf = conf_map.get(confidence_str, 1.0)
    
    try:
        sp_val = float(sp)
        if sp_val <= 0: sp_val = 1 # Избегаем деления на 0
    except:
        sp_val = 1
        
    rice = (reach * impact * conf * 10) / sp_val # Умножаем на 10 для красивого числа
    return round(rice, 1)

# --- 3. JIRA SYNC ---
def sync_jira_sheet(client, df_source):
    if df_source.empty:
        return

    sh = client.open("Quarterly Planning Data")
    try:
        ws_csv = sh.worksheet("csv")
    except:
        ws_csv = sh.add_worksheet(title="csv", rows=1000, cols=20)

    # Фильтруем только задачи с галочкой "Берем"
    df_active = df_source[df_source['Берем'].astype(str).str.upper() == 'TRUE'].copy()
    
    if df_active.empty:
        ws_csv.clear()
        return

    df_jira = pd.DataFrame()
    df_jira['Summary'] = df_active['Название задачи']
    
    # Добавляем RICE в Jira Description
    df_jira['Description'] = df_active['Описание'] + "\n\n" + \
                             "--- Planning Info ---\n" + \
                             "Author: " + df_active['Кто создал задачу'] + "\n" + \
                             "RICE Score: " + df_active['RICE'].astype(str) + "\n" + \
                             "Type: " + df_active['Тип']

    priority_map = {"P0 (Critical)": "Highest", "P1 (High)": "High", "P2 (Medium)": "Medium", "P3 (Low)": "Low"}
    df_jira['Priority'] = df_active['Приоритет'].map(priority_map).fillna("Medium")
    df_jira['Story Points'] = pd.to_numeric(df_active['Оценка (SP)'], errors='coerce').fillna(0)
    df_jira['Issue Type'] = "Story"
    df_jira['Labels'] = df_active['Заказчик'].str.replace(" ", "_") + ", Q_Planning"
    df_jira['Component'] = df_active['Исполнитель'] 

    ws_csv.clear()
    ws_csv.update([df_jira.columns.values.tolist()] + df_jira.values.tolist())

# --- 4. ANALYTICS SYNC ---
def update_analytics_tab(client, df_tasks, capacity_settings, clients_list):
    sh = client.open("Quarterly Planning Data")
    main_ws_name = sh.sheet1.title
    
    try:
        ws_an = sh.worksheet("Analytics_Data")
    except:
        ws_an = sh.add_worksheet(title="Analytics_Data", rows=1000, cols=20)
    
    ws_an.clear()
    
    headers_1 = ["Исполнитель", "Real Capacity (с учетом Threshold)", "Занято (Live Formula)", "Остаток"]
    rows_1 = []
    current_row = 2
    
    for team, settings in capacity_settings.items():
        total_days = settings['people'] * settings['days']
        overhead_percent = settings.get('overhead', 20)
        cap_val = round(total_days * (100 - overhead_percent) / 100.0, 1)
        
        # Индексы: Исполнитель(E), Оценка SP(I), Берем(A)
        formula_used = f"=SUMIFS('{main_ws_name}'!I:I; '{main_ws_name}'!E:E; A{current_row}; '{main_ws_name}'!A:A; TRUE)"
        formula_left = f"=B{current_row}-C{current_row}"
        
        rows_1.append([team, cap_val, formula_used, formula_left])
        current_row += 1
        
    ws_an.update(range_name='A1', values=[headers_1])
    ws_an.update(range_name='A2', values=rows_1, value_input_option='USER_ENTERED')
    
    start_row = len(rows_1) + 6
    
    for team in capacity_settings.keys():
        ws_an.update(range_name=f'A{start_row}', values=[[f"РАСПРЕДЕЛЕНИЕ: {team}"]])
        start_row += 1
        ws_an.update(range_name=f'A{start_row}', values=[["Заказчик", "SP (Checked Only)"]])
        start_row += 1
        
        team_rows = []
        for client_name in clients_list:
            # Исполнитель(E), Заказчик(F), Оценка SP(I), Берем(A)
            formula = f"=SUMIFS('{main_ws_name}'!I:I; '{main_ws_name}'!E:E; \"{team}\"; '{main_ws_name}'!F:F; A{start_row}; '{main_ws_name}'!A:A; TRUE)"
            team_rows.append([client_name, formula])
            start_row += 1
            
        write_range_start = start_row - len(clients_list)
        ws_an.update(range_name=f'A{write_range_start}', values=team_rows, value_input_option='USER_ENTERED')
        start_row += 2

# --- 5. ЧТЕНИЕ ДАННЫХ ---
def load_data():
    sheet = get_main_sheet()
    raw_data = sheet.get_all_values()
    
    expected_cols = ['Берем', 'Название задачи', 'Описание', 'Кто создал задачу', 'Исполнитель', 'Заказчик', 'Приоритет', 'RICE', 'Оценка (SP)', 'Reach', 'Impact', 'Confidence', 'Тип']
    
    if not raw_data:
        sheet.append_row(expected_cols)
        return pd.DataFrame(columns=expected_cols)

    if raw_data[0] != expected_cols:
        # Расширили диапазон обновления до столбца M (13-я колонка)
        sheet.update(range_name='A1:M1', values=[expected_cols])
        raw_data = sheet.get_all_values()

    headers = raw_data[0]
    data = raw_data[1:] if len(raw_data) > 1 else []
    df = pd.DataFrame(data, columns=headers)
    return df

# --- 6. СОХРАНЕНИЕ ---
def save_rows(rows_list):
    sheet = get_main_sheet()
    all_values = sheet.get_all_values()
    
    last_filled_row = 0
    for i, row in enumerate(all_values):
        if len(row) > 1 and row[1].strip():
            last_filled_row = i + 1
            
    target_row = last_filled_row + 1
    
    values_to_append = []
    for row_df in rows_list:
        values_to_append.append(row_df.values.tolist()[0])
        
    sheet.update(range_name=f'A{target_row}', values=values_to_append, value_input_option='USER_ENTERED')
    
    all_data = load_data()
    client = get_client()
    sync_jira_sheet(client, all_data)
    update_analytics_tab(client, all_data, st.session_state.capacity_settings, CLIENTS)

# --- 7. ПОНИЖЕНИЕ ПРИОРИТЕТА ---
def downgrade_existing_p0(executor_team):
    sheet = get_main_sheet()
    all_values = sheet.get_all_values()
    for i, row in enumerate(all_values):
        if i == 0: continue
        # Проверяем индексы с учетом новых колонок
        # Исполнитель = row[4], Приоритет = row[6], Тип = row[12]
        if (len(row) > 12 and row[4] == executor_team and row[6] == "P0 (Critical)" and row[12] == "Own Task"):
            sheet.update_cell(i + 1, 7, "P1 (High)") # 7 = колонка G (Приоритет)
            return True
    return False

# --- 8. ИНТЕРФЕЙС ---
st.title("📊 Quarterly Planning Tool")

DEPARTMENTS = ["Data Platform", "BI", "ML", "DA", "DE", "Data Ops", "WAS"]
CLIENTS = ["Data Department", "Partners", "Global Admin Panel", "Betting", "Casino", "Finance Core"]
PRIORITIES = ["P0 (Critical)", "P1 (High)", "P2 (Medium)", "P3 (Low)"]
SP_OPTIONS = [1, 2, 3, 5, 8]

if 'capacity_settings' not in st.session_state:
    st.session_state.capacity_settings = {dept: {'people': 5, 'days': 21, 'overhead': 20} for dept in DEPARTMENTS}

if st.button("🔄 Обновить данные из Таблицы"):
    df = load_data()
    client = get_client()
    sync_jira_sheet(client, df)
    update_analytics_tab(client, df, st.session_state.capacity_settings, CLIENTS)
    st.rerun()

# КОНФЛИКТ P0
if 'p0_conflict' not in st.session_state:
    st.session_state.p0_conflict = False
    st.session_state.pending_rows = []

if st.session_state.p0_conflict:
    st.warning(f"⚠️ **Внимание!** У команды уже есть задача с приоритетом P0 (Critical).")
    st.write("Может быть только 1 крит в плане.")
    st.write("**Понизить приоритет СУЩЕСТВУЮЩЕГО крита до P1 (High)?**")
    
    col_yes, col_no = st.columns(2)
    with col_yes:
        if st.button("ДА, понизить старый до P1, новый записать как P0"):
            executor = st.session_state.pending_rows[0]['Исполнитель'].iloc[0]
            downgrade_existing_p0(executor)
            save_rows(st.session_state.pending_rows)
            st.success("Готово! Перезапись выполнена.")
            st.session_state.p0_conflict = False
            st.session_state.pending_rows = []
            st.rerun()
    with col_no:
        if st.button("НЕТ, не трогать старый, новый записать как P1"):
            rows = st.session_state.pending_rows
            rows[0]['Приоритет'] = "P1 (High)"
            if len(rows) > 1:
                for r in rows[1:]: r['Приоритет'] = "P1 (High)"
            save_rows(rows)
            st.success("Готово! Сохранено как P1.")
            st.session_state.p0_conflict = False
            st.session_state.pending_rows = []
            st.rerun()
    st.stop()

# САЙДБАР
st.sidebar.header("⚙️ Ресурсы команд")
st.sidebar.info("Укажите значения и нажмите 'Пересчитать графики' в самом низу.")

with st.sidebar.form("capacity_form"):
    for dept in DEPARTMENTS:
        with st.expander(f"{dept}", expanded=False):
            cur_p = st.session_state.capacity_settings[dept].get('people', 5)
            cur_d = st.session_state.capacity_settings[dept].get('days', 21)
            cur_o = st.session_state.capacity_settings[dept].get('overhead', 20)
            
            p = st.number_input(f"{dept}: Человек", 1, 100, cur_p, key=f"p_{dept}")
            d = st.number_input(f"{dept}: Дней", 1, 60, cur_d, key=f"d_{dept}")
            o = st.number_input(f"{dept}: Threshold (минус от капасити)", 0, 100, cur_o, key=f"o_{dept}")
            
            st.session_state.capacity_settings[dept] = {'people': p, 'days': d, 'overhead': o}
            
    submit_capacity = st.form_submit_button("📊 Пересчитать графики")

if submit_capacity:
    df_for_update = load_data()
    client_for_update = get_client()
    update_analytics_tab(client_for_update, df_for_update, st.session_state.capacity_settings, CLIENTS)
    st.sidebar.success("✅ Значения обновлены в Гугл Таблице!")

# ФОРМА ЗАДАЧИ
st.subheader("➕ Создание задачи")

with st.form("main_form", clear_on_submit=True):
    main_team = st.selectbox("Чья задача? (Кто исполнитель)", DEPARTMENTS)
    task_name = st.text_input("Название задачи", placeholder="Краткая суть...")
    description = st.text_area("Описание задачи", placeholder="Детали, DoD...", height=100)
    
    col_cl, col_pr, col_sp = st.columns(3)
    with col_cl: client = st.selectbox("Заказчик (Стрим/Продукт)", CLIENTS)
    with col_pr: priority = st.selectbox("Приоритет", PRIORITIES, index=2)
    with col_sp: estimate = st.select_slider("Оценка в SP (Своей задачи)", options=SP_OPTIONS, value=1)

    st.markdown("---")
    
    # === НОВЫЙ БЛОК RICE ===
    st.markdown("### 🔬 RICE Оценка (Интуитивно)")
    st.caption("Помогает алгоритму понять ценность. Укажите интуитивно, система сама посчитает и добавит SP.")
    
    col_r, col_i, col_c = st.columns(3)
    with col_r:
        reach_val = st.slider("Охват (Reach)", min_value=1, max_value=10, value=5, help="Сколько пользователей затронет? 1 = единицы, 10 = абсолютно все.")
    with col_i:
        impact_val = st.slider("Влияние (Impact)", min_value=1, max_value=5, value=3, help="Какую пользу принесет? 1 = незаметно, 5 = критически важно.")
    with col_c:
        conf_val = st.selectbox("Уверенность (Confidence)", ["100% (Уверен)", "80% (Скорее уверен)", "50% (Интуиция)"])
    
    st.markdown("---")
    
    st.markdown("### 🔗 Зависимость №1")
    col_d1_1, col_d1_2 = st.columns([1, 2])
    with col_d1_1: dep1_type = st.radio("Тип №1:", ["Блокер", "Энейблер"], horizontal=True, key="d1_type")
    with col_d1_2: dep1_team = st.selectbox("Команда №1:", ["(Нет зависимости)"] + DEPARTMENTS, key="d1_team")
    dep1_name = st.text_input("Название задачи для Команды №1", key="d1_name")
    dep1_desc = st.text_area("Описание требований №1", height=68, key="d1_desc")
    
    st.markdown("---")

    st.markdown("### 🔗 Зависимость №2")
    col_d2_1, col_d2_2 = st.columns([1, 2])
    with col_d2_1: dep2_type = st.radio("Тип №2:", ["Блокер", "Энейблер"], horizontal=True, key="d2_type")
    with col_d2_2: dep2_team = st.selectbox("Команда №2:", ["(Нет зависимости)"] + DEPARTMENTS, key="d2_team")
    dep2_name = st.text_input("Название задачи для Команды №2", key="d2_name")
    dep2_desc = st.text_area("Описание требований №2", height=68, key="d2_desc")

    submitted = st.form_submit_button("Сохранить задачу")

    if submitted:
        if not task_name:
            st.error("Введите название основной задачи!")
        else:
            rows_to_save = []
            
            # Считаем RICE и графики для основной задачи
            calculated_rice = calculate_rice(reach_val, impact_val, conf_val, estimate)
            reach_bar = make_text_bar(reach_val, 10)
            impact_bar = make_text_bar(impact_val, 5)
            
            rows_to_save.append(pd.DataFrame([{
                'Берем': 'TRUE', 
                'Название задачи': task_name,
                'Описание': description,
                'Кто создал задачу': main_team,
                'Исполнитель': main_team,
                'Заказчик': client,
                'Приоритет': priority,
                'RICE': calculated_rice,
                'Оценка (SP)': estimate,
                'Reach': reach_bar,
                'Impact': impact_bar,
                'Confidence': conf_val,
                'Тип': 'Own Task'
            }]))
            
            # Для зависимостей RICE и SP оставляем пустыми (они оценят сами)
            if dep1_team != "(Нет зависимости)" and dep1_team != main_team:
                if dep1_name:
                    g_type = "Incoming Blocker" if dep1_type == "Блокер" else "Incoming Enabler"
                    rows_to_save.append(pd.DataFrame([{
                        'Берем': 'TRUE',
                        'Название задачи': dep1_name,
                        '
