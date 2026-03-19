import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import datetime

# 1. Настройка страницы
st.set_page_config(page_title="Quarterly Planning", layout="wide")

# КОНСТАНТЫ
DEPARTMENTS = ["Data Platform", "BI", "ML", "DA", "DE", "Data Ops", "WAS"]
CLIENTS = ["Data Department", "Partners", "Global Admin Panel", "Betting", "Casino", "Finance Core"]
PRIORITIES = ["P0 (Critical)", "P1 (High)", "P2 (Medium)", "P3 (Low)"]
SP_OPTIONS = [1, 2, 3, 5, 8]

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

# --- 3. РАБОТА С НАСТРОЙКАМИ CAPACITY ---
def load_capacity_settings(client, departments_list):
    sh = client.open("Quarterly Planning Data")
    
    try:
        ws = sh.worksheet("Capacity_Settings")
    except:
        ws = sh.add_worksheet(title="Capacity_Settings", rows=50, cols=4)
        
    raw_data = ws.get_all_values()
    expected_cols = ["Team", "People", "Days", "Threshold"]
    
    if not raw_data or raw_data[0] != expected_cols:
        ws.clear()
        default_rows = [expected_cols]
        default_settings = {}
        for dept in departments_list:
            default_rows.append([dept, 5, 21, 20])
            default_settings[dept] = {'people': 5, 'days': 21, 'overhead': 20}
        ws.update(range_name='A1', values=default_rows)
        return default_settings
        
    settings = {}
    for row in raw_data[1:]:
        if len(row) >= 4:
            team = row[0]
            try:
                p, d, o = int(row[1]), int(row[2]), int(row[3])
            except ValueError:
                p, d, o = 5, 21, 20
                
            if team in departments_list:
                settings[team] = {'people': p, 'days': d, 'overhead': o}
                
    for dept in departments_list:
        if dept not in settings:
            settings[dept] = {'people': 5, 'days': 21, 'overhead': 20}
            
    return settings

def save_capacity_settings(client, settings_dict):
    sh = client.open("Quarterly Planning Data")
    ws = sh.worksheet("Capacity_Settings")
    
    rows = [["Team", "People", "Days", "Threshold"]]
    for team, vals in settings_dict.items():
        rows.append([team, vals['people'], vals['days'], vals['overhead']])
        
    ws.clear()
    ws.update(range_name='A1', values=rows)

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ RICE ---
def make_text_bar(val, max_val):
    try:
        val = int(val)
    except:
        val = 0
    filled = "█" * val
    empty = "░" * (max_val - val)
    return f"{filled} {val}/{max_val}"

# --- 4. JIRA SYNC ---
def sync_jira_sheet(client, df_source):
    if df_source.empty:
        return

    sh = client.open("Quarterly Planning Data")
    try:
        ws_csv = sh.worksheet("csv")
    except:
        ws_csv = sh.add_worksheet(title="csv", rows=1000, cols=20)

    df_active = df_source[df_source['Берем'].astype(str).str.upper() == 'TRUE'].copy()
    
    if df_active.empty:
        ws_csv.clear()
        return

    df_jira = pd.DataFrame()
    df_jira['Summary'] = df_active['Название задачи']
    
    # Добавили даты в описание для Jira
    df_jira['Description'] = df_active['Описание'] + "\n\n" + \
                             "--- Planning Info ---\n" + \
                             "Author: " + df_active['Кто создал задачу'] + "\n" + \
                             "RICE Score: " + df_active['RICE'].astype(str) + "\n" + \
                             "Start: " + df_active['Start date'].astype(str) + "\n" + \
                             "End: " + df_active['End date'].astype(str) + "\n" + \
                             "Type: " + df_active['Тип']

    priority_map = {"P0 (Critical)": "Highest", "P1 (High)": "High", "P2 (Medium)": "Medium", "P3 (Low)": "Low"}
    df_jira['Priority'] = df_active['Приоритет'].map(priority_map).fillna("Medium")
    df_jira['Story Points'] = pd.to_numeric(df_active['Оценка (SP)'], errors='coerce').fillna(0)
    df_jira['Issue Type'] = "Story"
    df_jira['Labels'] = df_active['Заказчик'].str.replace(" ", "_") + ", Q_Planning"
    df_jira['Component'] = df_active['Исполнитель'] 

    ws_csv.clear()
    ws_csv.update([df_jira.columns.values.tolist()] + df_jira.values.tolist())

# --- 5. ANALYTICS SYNC ---
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
            formula = f"=SUMIFS('{main_ws_name}'!I:I; '{main_ws_name}'!E:E; \"{team}\"; '{main_ws_name}'!F:F; A{start_row}; '{main_ws_name}'!A:A; TRUE)"
            team_rows.append([client_name, formula])
            start_row += 1
            
        write_range_start = start_row - len(clients_list)
        ws_an.update(range_name=f'A{write_range_start}', values=team_rows, value_input_option='USER_ENTERED')
        start_row += 2

# --- 6. ЧТЕНИЕ ДАННЫХ ---
def load_data():
    sheet = get_main_sheet()
    raw_data = sheet.get_all_values()
    
    # Добавлены Start date и End date
    expected_cols = ['Берем', 'Название задачи', 'Описание', 'Кто создал задачу', 'Исполнитель', 'Заказчик', 'Приоритет', 'RICE', 'Оценка (SP)', 'Reach', 'Impact', 'Confidence', 'Тип', 'Start date', 'End date']
    
    if not raw_data:
        sheet.append_row(expected_cols)
        return pd.DataFrame(columns=expected_cols)

    if raw_data[0] != expected_cols:
        # Обновляем заголовки до колонки O (15-я колонка)
        sheet.update(range_name='A1:O1', values=[expected_cols])
        raw_data = sheet.get_all_values()

    headers = raw_data[0]
    data = raw_data[1:] if len(raw_data) > 1 else []
    df = pd.DataFrame(data, columns=headers)
    return df

# --- 7. СОХРАНЕНИЕ ЗАДАЧ ---
def save_rows(rows_list):
    sheet = get_main_sheet()
    all_values = sheet.get_all_values()
    
    last_filled_row = 0
    for i, row in enumerate(all_values):
        if len(row) > 1 and row[1].strip():
            last_filled_row = i + 1
            
    target_row = last_filled_row + 1
    
    values_to_append = []
    for idx, row_df in enumerate(rows_list):
        row_data = row_df.values.tolist()[0]
        current_row = target_row + idx
        
        # J = Reach, K = Impact, L = Confidence (%), I = SP
        rice_formula = f'=IFERROR(ROUND(((J{current_row} * K{current_row} * L{current_row}) / I{current_row}) * 100; -1); "")'
        row_data[7] = rice_formula 
        values_to_append.append(row_data)
        
    sheet.update(range_name=f'A{target_row}', values=values_to_append, value_input_option='USER_ENTERED')
    
    all_data = load_data()
    client = get_client()
    sync_jira_sheet(client, all_data)
    update_analytics_tab(client, all_data, st.session_state.capacity_settings, CLIENTS)

# --- 8. ПОНИЖЕНИЕ ПРИОРИТЕТА ---
def downgrade_existing_p0(executor_team):
    sheet = get_main_sheet()
    all_values = sheet.get_all_values()
    for i, row in enumerate(all_values):
        if i == 0: continue
        if (len(row) > 12 and row[4] == executor_team and row[6] == "P0 (Critical)" and row[12] == "Own Task"):
            sheet.update_cell(i + 1, 7, "P1 (High)") 
            return True
    return False

# --- ИНИЦИАЛИЗАЦИЯ НАСТРОЕК (ИЗ ГУГЛ ТАБЛИЦЫ) ---
if 'capacity_settings' not in st.session_state:
    st.session_state.capacity_settings = load_capacity_settings(get_client(), DEPARTMENTS)

# --- ИНТЕРФЕЙС ---
st.title("📊 Quarterly Planning Tool")

if st.button("🔄 Обновить данные из Таблицы"):
    df = load_data()
    client = get_client()
    st.session_state.capacity_settings = load_capacity_settings(client, DEPARTMENTS)
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

# САЙДБАР (С СОХРАНЕНИЕМ)
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
    client_for_update = get_client()
    save_capacity_settings(client_for_update, st.session_state.capacity_settings)
    df_for_update = load_data()
    update_analytics_tab(client_for_update, df_for_update, st.session_state.capacity_settings, CLIENTS)
    st.sidebar.success("✅ Значения сохранены в таблицу и графики обновлены!")

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
    
    # === БЛОК ДАТ ===
    st.markdown("### 🗓 Сроки (Необязательно)")
    st.caption("Если оставить пустыми, система начнет задачу со следующего месяца и прибавит SP.")
    col_sd, col_ed = st.columns(2)
    with col_sd:
        start_date_input = st.date_input("Дата начала (Start date)", value=None, format="DD.MM.YYYY")
    with col_ed:
        end_date_input = st.date_input("Дата конца (End date)", value=None, format="DD.MM.YYYY")

    st.markdown("---")
    
    # === БЛОК RICE ===
    st.markdown("### 🔬 RICE Оценка (Интуитивно)")
    
    col_r, col_i, col_c = st.columns(3)
    with col_r:
        reach_val = st.slider("Охват (Reach)", min_value=1, max_value=10, value=5)
        st.caption("Сколько дашбордов, витрин или систем затронет? (1 = один ad-hoc отчет, 10 = всё DWH или ключевой пайплайн)")
    with col_i:
        impact_val = st.slider("Влияние (Impact)", min_value=1, max_value=5, value=3)
        st.caption("Какая польза бизнесу или архитектуре? (1 = минорный рефакторинг, 5 = спасение прода / х10 ускорение / прямой доход)")
    with col_c:
        conf_val_str = st.selectbox("Уверенность (Confidence)", ["100% (Уверен)", "80% (Скорее уверен)", "50% (Интуиция)"])
        st.caption("Насколько точна наша оценка?")
        
        conf_map = {"100% (Уверен)": "100%", "80% (Скорее уверен)": "80%", "50% (Интуиция)": "50%"}
        conf_val_num = conf_map.get(conf_val_str, "100%")
        
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
            # === РАСЧЕТ ДАТ ===
            today = datetime.date.today()
            if today.month == 12:
                next_month_start = datetime.date(today.year + 1, 1, 1)
            else:
                next_month_start = datetime.date(today.year, today.month + 1, 1)

            sp_val = int(estimate)

            # Логика расчета дат в зависимости от того, что ввел пользователь
            if start_date_input is None and end_date_input is None:
                final_start = next_month_start
                final_end = final_start + datetime.timedelta(days=sp_val)
            elif start_date_input is None and end_date_input is not None:
                final_end = end_date_input
                final_start = final_end - datetime.timedelta(days=sp_val)
            elif start_date_input is not None and end_date_input is None:
                final_start = start_date_input
                final_end = final_start + datetime.timedelta(days=sp_val)
            else:
                final_start = start_date_input
                final_end = end_date_input

            # Форматируем в строку (YYYY-MM-DD) для Гугл Таблицы
            str_start = final_start.strftime("%Y-%m-%d")
            str_end = final_end.strftime("%Y-%m-%d")

            rows_to_save = []
            
            # Основная задача
            rows_to_save.append(pd.DataFrame([{
                'Берем': 'TRUE', 
                'Название задачи': task_name,
                'Описание': description,
                'Кто создал задачу': main_team,
                'Исполнитель': main_team,
                'Заказчик': client,
                'Приоритет': priority,
                'RICE': "", 
                'Оценка (SP)': estimate,
                'Reach': reach_val,         
                'Impact': impact_val,       
                'Confidence': conf_val_num, 
                'Тип': 'Own Task',
                'Start date': str_start,
                'End date': str_end
            }]))
            
            # Зависимости (Даты оставляем пустыми, так как планировать их будет другая команда)
            if dep1_team != "(Нет зависимости)" and dep1_team != main_team:
                if dep1_name:
                    g_type = "Incoming Blocker" if dep1_type == "Блокер" else "Incoming Enabler"
                    rows_to_save.append(pd.DataFrame([{
                        'Берем': 'TRUE',
                        'Название задачи': dep1_name,
                        'Описание': dep1_desc,
                        'Кто создал задачу': main_team,
                        'Исполнитель': dep1_team,
                        'Заказчик': client,
                        'Приоритет': priority,
                        'RICE': "", 
                        'Оценка (SP)': "",
                        'Reach': reach_val,         
                        'Impact': impact_val,       
                        'Confidence': conf_val_num, 
                        'Тип': g_type,
                        'Start date': "",
                        'End date': ""
                    }]))
            
            if dep2_team != "(Нет зависимости)" and dep2_team != main_team:
                if dep2_name:
                    g_type = "Incoming Blocker" if dep2_type == "Блокер" else "Incoming Enabler"
                    rows_to_save.append(pd.DataFrame([{
                        'Берем': 'TRUE',
                        'Название задачи': dep2_name,
                        'Описание': dep2_desc,
                        'Кто создал задачу': main_team,
                        'Исполнитель': dep2_team,
                        'Заказчик': client,
                        'Приоритет': priority,
                        'RICE': "", 
                        'Оценка (SP)': "",
                        'Reach': reach_val,         
                        'Impact': impact_val,       
                        'Confidence': conf_val_num, 
                        'Тип': g_type,
                        'Start date': "",
                        'End date': ""
                    }]))

            if priority == "P0 (Critical)":
                current_df = load_data()
                existing_p0 = current_df[
                    (current_df['Исполнитель'] == main_team) & 
                    (current_df['Приоритет'] == 'P0 (Critical)') &
                    (current_df['Тип'] == 'Own Task') &
                    (current_df['Берем'].astype(str).str.upper() == 'TRUE')
                ]
                if not existing_p0.empty:
                    st.session_state.p0_conflict = True
                    st.session_state.pending_rows = rows_to_save
                    st.rerun()
            
            save_rows(rows_to_save)
            st.success("Данные и даты успешно сохранены!")
            st.rerun()

# АНАЛИТИКА (ГРАФИКИ)
try:
    df_tasks = load_data()
except:
    df_tasks = pd.DataFrame()

if not df_tasks.empty:
    st.divider()
    df_tasks_active = df_tasks[df_tasks['Берем'].astype(str).str.upper() == 'TRUE'].copy()
    df_tasks_active['Оценка (SP)'] = pd.to_numeric(df_tasks_active['Оценка (SP)'], errors='coerce').fillna(0)
    
    cap_data = []
    for d, s in st.session_state.capacity_settings.items():
        total = s['people'] * s['days']
        overhead = s.get('overhead', 20)
        real_cap = round(total * (100 - overhead) / 100.0, 1)
        cap_data.append({'Исполнитель': d, 'Real Capacity': real_cap})
        
    df_cap = pd.DataFrame(cap_data)
    usage = df_tasks_active.groupby(['Исполнитель', 'Тип'])['Оценка (SP)'].sum().reset_index()
    
    st.subheader("📊 Загрузка команд (С учетом Threshold)")
    fig = go.Figure()
    
    fig.add_trace(go.Bar(x=df_cap['Исполнитель'], y=df_cap['Real Capacity'], name='Real Capacity', marker_color='lightgrey', text=df_cap['Real Capacity'], textposition='auto'))
    
    for t in ['Own Task', 'Incoming Blocker', 'Incoming Enabler']:
        sub = usage[usage['Тип'] == t]
        if not sub.empty:
            fig.add_trace(go.Bar(x=sub['Исполнитель'], y=sub['Оценка (SP)'], name=t, text=sub['Оценка (SP)'], textposition='inside'))
            
    fig.update_layout(barmode='overlay', title="Real Capacity vs Workload")
    st.plotly_chart(fig, use_container_width=True)
    
    st.subheader("📋 Список всех задач")
    st.dataframe(df_tasks, use_container_width=True)
