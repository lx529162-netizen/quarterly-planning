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

# --- 3. JIRA SYNC ---
def sync_jira_sheet(client, df_source):
    if df_source.empty:
        return

    sh = client.open("Quarterly Planning Data")
    try:
        ws_csv = sh.worksheet("csv")
    except:
        ws_csv = sh.add_worksheet(title="csv", rows=1000, cols=20)

    # Фильтруем только те задачи, где стоит галочка "Берем" (TRUE)
    # Приводим к строке, чтобы точно найти TRUE
    df_active = df_source[df_source['Берем'].astype(str).str.upper() == 'TRUE'].copy()
    
    if df_active.empty:
        ws_csv.clear()
        return

    df_jira = pd.DataFrame()
    df_jira['Summary'] = df_active['Название задачи']
    df_jira['Description'] = df_active['Описание'] + "\n\n" + \
                             "--- Planning Info ---\n" + \
                             "Author: " + df_active['Кто создал задачу'] + "\n" + \
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
    
    # === ТАБЛИЦА 1: CAPACITY ===
    headers_1 = ["Исполнитель", "Total Capacity", "Занято (Live Formula)", "Остаток"]
    rows_1 = []
    current_row = 2
    
    for team, settings in capacity_settings.items():
        cap_val = settings['people'] * settings['days']
        
        # СМЕЩЕНИЕ КОЛОНОК (Т.к. добавили "Берем" в A):
        # Исполнитель был D -> стал E
        # SP был G -> стал H
        # Мы учитываем в формуле только те задачи, где "Берем" (Колонка A) = TRUE
        
        # СУММЕСЛИМН( ДиапазонСуммы; ДиапазонКритерия1; Критерий1; ДиапазонКритерия2; Критерий2 )
        # H:H - Сумма (SP)
        # E:E - Исполнитель
        # A:A - Берем (TRUE)
        
        formula_used = f"=SUMIFS('{main_ws_name}'!H:H; '{main_ws_name}'!E:E; A{current_row}; '{main_ws_name}'!A:A; TRUE)"
        
        formula_left = f"=B{current_row}-C{current_row}"
        rows_1.append([team, cap_val, formula_used, formula_left])
        current_row += 1
        
    ws_an.update(range_name='A1', values=[headers_1])
    ws_an.update(range_name='A2', values=rows_1, value_input_option='USER_ENTERED')
    
    # === ТАБЛИЦЫ ПО КОМАНДАМ ===
    start_row = len(rows_1) + 6
    
    for team in capacity_settings.keys():
        ws_an.update(range_name=f'A{start_row}', values=[[f"РАСПРЕДЕЛЕНИЕ: {team}"]])
        start_row += 1
        ws_an.update(range_name=f'A{start_row}', values=[["Заказчик", "SP (Checked Only)"]])
        start_row += 1
        
        team_rows = []
        for client_name in clients_list:
            # E = Исполнитель, F = Заказчик, H = SP, A = Берем
            formula = f"=SUMIFS('{main_ws_name}'!H:H; '{main_ws_name}'!E:E; \"{team}\"; '{main_ws_name}'!F:F; A{start_row}; '{main_ws_name}'!A:A; TRUE)"
            
            team_rows.append([client_name, formula])
            start_row += 1
            
        write_range_start = start_row - len(clients_list)
        ws_an.update(range_name=f'A{write_range_start}', values=team_rows, value_input_option='USER_ENTERED')
        start_row += 2


# --- 5. ЧТЕНИЕ ДАННЫХ ---
def load_data():
    sheet = get_main_sheet()
    raw_data = sheet.get_all_values()
    
    # Добавили колонку 'Берем' в начало
    expected_cols = ['Берем', 'Название задачи', 'Описание', 'Кто создал задачу', 'Исполнитель', 'Заказчик', 'Приоритет', 'Оценка (SP)', 'Тип']
    
    if not raw_data:
        sheet.append_row(expected_cols)
        return pd.DataFrame(columns=expected_cols)

    # Проверяем заголовки
    if raw_data[0] != expected_cols:
        # Если заголовки старые, пробуем обновить.
        # Внимание: если столбцов не хватает, это может быть опасно, 
        # но gspread обычно сдвигает данные.
        sheet.update(range_name='A1:I1', values=[expected_cols])
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
        # Проверяем 2-ю колонку (Название задачи), т.к. 1-я может быть просто чекбоксом
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
        # Индексы сдвинулись на +1 из-за колонки "Берем"
        # 4 = Исполнитель (E), 6 = Приоритет (G), 8 = Тип (I)
        if (len(row) > 8 and row[4] == executor_team and row[6] == "P0 (Critical)" and row[8] == "Own Task"):
            sheet.update_cell(i + 1, 7, "P1 (High)") # Колонка G - это 7-я
            return True
    return False

# --- 8. ИНТЕРФЕЙС ---
st.title("📊 Quarterly Planning Tool")

DEPARTMENTS = ["Data Platform", "BI", "ML", "DA", "DE", "Data Ops", "WAS"]
CLIENTS = ["Data Department", "Partners", "Global Admin Panel", "Betting", "Casino", "Finance Core"]
PRIORITIES = ["P0 (Critical)", "P1 (High)", "P2 (Medium)", "P3 (Low)"]
SP_OPTIONS = [1, 2, 3, 5, 8]

if 'capacity_settings' not in st.session_state:
    st.session_state.capacity_settings = {dept: {'people': 5, 'days': 21} for dept in DEPARTMENTS}

if st.button("🔄 Обновить данные"):
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
st.sidebar.info("1 SP = 1 Человеко-день")
for dept in DEPARTMENTS:
    with st.sidebar.expander(f"{dept}", expanded=False):
        p = st.number_input(f"{dept}: Человек", 1, 100, 5, key=f"p_{dept}")
        d = st.number_input(f"{dept}: Дней", 1, 60, 21, key=f"d_{dept}")
        st.session_state.capacity_settings[dept] = {'people': p, 'days': d}

# ФОРМА
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

    submitted = st.form_submit_button("Сохранить всё")

    if submitted:
        if not task_name:
            st.error("Введите название основной задачи!")
        else:
            rows_to_save = []
            
            # Мы пишем "TRUE" (строку), чтобы Гугл Таблица, 
            # где настроен формат чекбокса, поняла это как галочку.
            rows_to_save.append(pd.DataFrame([{
                'Берем': 'TRUE', 
                'Название задачи': task_name,
                'Описание': description,
                'Кто создал задачу': main_team,
                'Исполнитель': main_team,
                'Заказчик': client,
                'Приоритет': priority,
                'Оценка (SP)': estimate,
                'Тип': 'Own Task'
            }]))
            
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
                        'Оценка (SP)': "",
                        'Тип': g_type
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
                        'Оценка (SP)': "",
                        'Тип': g_type
                    }]))

            if priority == "P0 (Critical)":
                current_df = load_data()
                existing_p0 = current_df[
                    (current_df['Исполнитель'] == main_team) & 
                    (current_df['Приоритет'] == 'P0 (Critical)') &
                    (current_df['Тип'] == 'Own Task') &
                    (current_df['Берем'].astype(str).str.upper() == 'TRUE') # Проверяем только взятые задачи
                ]
                if not existing_p0.empty:
                    st.session_state.p0_conflict = True
                    st.session_state.pending_rows = rows_to_save
                    st.rerun()
            
            save_rows(rows_to_save)
            st.success("Данные сохранены! (Графики считают только задачи с галочкой 'Берем')")
            st.rerun()

# АНАЛИТИКА
try:
    df_tasks = load_data()
except:
    df_tasks = pd.DataFrame()

if not df_tasks.empty:
    st.divider()
    # Фильтруем для графика в приложении тоже
    # Показываем только TRUE
    df_tasks_active = df_tasks[df_tasks['Берем'].astype(str).str.upper() == 'TRUE'].copy()
    
    df_tasks_active['Оценка (SP)'] = pd.to_numeric(df_tasks_active['Оценка (SP)'], errors='coerce').fillna(0)
    
    cap_data = [{'Исполнитель': d, 'Total Capacity': s['people']*s['days']} for d, s in st.session_state.capacity_settings.items()]
    df_cap = pd.DataFrame(cap_data)
    usage = df_tasks_active.groupby(['Исполнитель', 'Тип'])['Оценка (SP)'].sum().reset_index()
    
    st.subheader("📊 Загрузка команд (Только 'Берем')")
    fig = go.Figure()
    fig.add_trace(go.Bar(x=df_cap['Исполнитель'], y=df_cap['Total Capacity'], name='Total Capacity', marker_color='lightgrey'))
    for t in ['Own Task', 'Incoming Blocker', 'Incoming Enabler']:
        sub = usage[usage['Тип'] == t]
        if not sub.empty:
            fig.add_trace(go.Bar(x=sub['Исполнитель'], y=sub['Оценка (SP)'], name=t, text=sub['Оценка (SP)'], textposition='auto'))
    fig.update_layout(barmode='overlay', title="Capacity vs Workload")
    st.plotly_chart(fig, use_container_width=True)
    
    st.subheader("📋 Список всех задач")
    # Показываем таблицу целиком (даже без галочек, чтобы можно было проверить)
    st.dataframe(df_tasks, use_container_width=True)
