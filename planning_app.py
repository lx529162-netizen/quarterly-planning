import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- 1. НАСТРОЙКА СТРАНИЦЫ ---
st.set_page_config(page_title="Quarterly Planning", layout="wide")

# --- 2. ПОДКЛЮЧЕНИЕ К GOOGLE SHEETS ---
def get_client():
    try:
        # Проверяем наличие секретов (для Streamlit Cloud)
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
    # Открываем основную таблицу
    return client.open("Quarterly Planning Data").sheet1

# --- 3. ФУНКЦИЯ СИНХРОНИЗАЦИИ С JIRA (ЛИСТ 'csv') ---
def sync_jira_sheet(df_source):
    if df_source.empty:
        return

    client = get_client()
    sh = client.open("Quarterly Planning Data")
    
    # Пытаемся открыть лист 'csv', если нет - создаем
    try:
        ws_csv = sh.worksheet("csv")
    except:
        ws_csv = sh.add_worksheet(title="csv", rows=1000, cols=20)

    # Подготовка DataFrame для Jira
    df_jira = pd.DataFrame()

    # Mapping полей
    df_jira['Summary'] = df_source['Task Name']
    
    # Формируем богатое описание
    df_jira['Description'] = df_source['Description'] + "\n\n" + \
                             "--- Planning Info ---\n" + \
                             "Internal Requester: " + df_source['Requester'] + "\n" + \
                             "Internal Type: " + df_source['Type']

    # Mapping Приоритетов
    priority_map = {
        "P0 (Critical)": "Highest",
        "P1 (High)": "High",
        "P2 (Medium)": "Medium",
        "P3 (Low)": "Low"
    }
    df_jira['Priority'] = df_source['Priority'].map(priority_map).fillna("Medium")

    # Story Points
    df_jira['Story Points'] = pd.to_numeric(df_source['Estimate (SP)'], errors='coerce').fillna(0)

    # Issue Type
    df_jira['Issue Type'] = "Story"

    # Labels (Превращаем Клиента в тег, убираем пробелы)
    df_jira['Labels'] = df_source['Client'].str.replace(" ", "_") + ", Q_Planning"

    # Component (Исполнитель)
    df_jira['Component'] = df_source['Executor'] 

    # Перезаписываем лист csv
    ws_csv.clear()
    ws_csv.update([df_jira.columns.values.tolist()] + df_jira.values.tolist())

# --- 4. ЧТЕНИЕ ДАННЫХ ---
def load_data():
    sheet = get_main_sheet()
    raw_data = sheet.get_all_values()
    
    expected_cols = ['Task Name', 'Description', 'Requester', 'Executor', 'Client', 'Priority', 'Estimate (SP)', 'Type']
    
    # Если таблица пустая
    if not raw_data:
        sheet.append_row(expected_cols)
        return pd.DataFrame(columns=expected_cols)

    # Если заголовки не совпадают (старая версия)
    if raw_data[0] != expected_cols:
        sheet.update(range_name='A1:H1', values=[expected_cols])
        raw_data = sheet.get_all_values()

    headers = raw_data[0]
    data = raw_data[1:] if len(raw_data) > 1 else []
    
    df = pd.DataFrame(data, columns=headers)
    return df

# --- 5. СОХРАНЕНИЕ (Умная запись ВНУТРЬ таблицы) ---
def save_rows(rows_list):
    sheet = get_main_sheet()
    all_values = sheet.get_all_values()
    
    # Ищем последнюю заполненную строку (где есть текст в первой колонке)
    last_filled_row = 0
    for i, row in enumerate(all_values):
        if row and len(row) > 0 and row[0].strip():
            last_filled_row = i + 1
            
    # Пишем в следующую строку
    target_row = last_filled_row + 1
    
    # Подготовка данных
    values_to_append = []
    for row_df in rows_list:
        values_to_append.append(row_df.values.tolist()[0])
        
    # Записываем данные в конкретный диапазон
    sheet.update(range_name=f'A{target_row}', values=values_to_append)
    
    # Сразу обновляем лист для Jira
    all_data = load_data() 
    sync_jira_sheet(all_data)

# --- 6. ПОНИЖЕНИЕ ПРИОРИТЕТА (Для конфликтов P0) ---
def downgrade_existing_p0(executor_team):
    sheet = get_main_sheet()
    all_values = sheet.get_all_values()
    
    for i, row in enumerate(all_values):
        if i == 0: continue
        # Индексы: 3=Executor, 5=Priority, 7=Type
        if (len(row) > 7 and 
            row[3] == executor_team and 
            row[5] == "P0 (Critical)" and 
            row[7] == "Own Task"):
            
            row_number = i + 1
            # Меняем ячейку F (Priority) на P1
            sheet.update_cell(row_number, 6, "P1 (High)")
            return True
    return False

# --- 7. ИНТЕРФЕЙС И ЛОГИКА ---
st.title("📊 Quarterly Planning Tool")

if st.button("🔄 Обновить данные"):
    # Принудительно синхронизируем Jira при обновлении
    df = load_data()
    sync_jira_sheet(df)
    st.rerun()

# --- КОНСТАНТЫ ---
DEPARTMENTS = ["Data Platform", "BI", "ML", "DA", "DE", "Data Ops", "WAS"]
CLIENTS = ["Data Department", "Partners", "Global Admin Panel", "Betting", "Casino", "Finance Core"]
PRIORITIES = ["P0 (Critical)", "P1 (High)", "P2 (Medium)", "P3 (Low)"]
SP_OPTIONS = [1, 2, 3, 5, 8]

if 'capacity_settings' not in st.session_state:
    st.session_state.capacity_settings = {dept: {'people': 5, 'days': 21} for dept in DEPARTMENTS}

# --- ОБРАБОТКА КОНФЛИКТА P0 ---
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
            # Понижаем старый
            executor = st.session_state.pending_rows[0]['Executor'].iloc[0]
            downgrade_existing_p0(executor)
            # Сохраняем новый
            save_rows(st.session_state.pending_rows)
            
            st.success("Готово! Старый крит стал P1, новый записан как P0.")
            st.session_state.p0_conflict = False
            st.session_state.pending_rows = []
            st.rerun()

    with col_no:
        if st.button("НЕТ, не трогать старый, новый записать как P1"):
            # Меняем приоритет у новой задачи
            rows = st.session_state.pending_rows
            rows[0]['Priority'] = "P1 (High)"
            if len(rows) > 1:
                rows[1]['Priority'] = "P1 (High)" # Блокеру тоже
            
            save_rows(rows)
            st.success("Готово! Новая задача сохранена как P1.")
            st.session_state.p0_conflict = False
            st.session_state.pending_rows = []
            st.rerun()
            
    st.stop() # Останавливаем рендеринг формы, пока не решен конфликт

# --- САЙДБАР ---
st.sidebar.header("⚙️ Ресурсы команд")
st.sidebar.info("1 SP = 1 Человеко-день")
for dept in DEPARTMENTS:
    with st.sidebar.expander(f"{dept}", expanded=False):
        p = st.number_input(f"{dept}: Человек", 1, 100, 5, key=f"p_{dept}")
        d = st.number_input(f"{dept}: Дней", 1, 60, 21, key=f"d_{dept}")
        st.session_state.capacity_settings[dept] = {'people': p, 'days': d}

# --- ФОРМА СОЗДАНИЯ ЗАДАЧИ ---
st.subheader("➕ Создание задачи")

with st.form("main_form", clear_on_submit=True):
    # 1. Чья задача
    main_team = st.selectbox("Чья задача? (Кто исполнитель)", DEPARTMENTS)
    
    # 2. Описание
    task_name = st.text_input("Название задачи", placeholder="Краткая суть...")
    description = st.text_area("Описание задачи", placeholder="Детали, DoD...", height=100)
    
    col_client, col_prio, col_sp = st.columns(3)
    with col_client:
        client = st.selectbox("Заказчик (Стрим/Продукт)", CLIENTS)
    with col_prio:
        priority = st.selectbox("Приоритет", PRIORITIES)
    with col_sp:
        estimate = st.select_slider("Оценка в SP (Своей задачи)", options=SP_OPTIONS, value=1)

    st.markdown("---")
    
    # --- БЛОКЕР ---
    st.markdown("### 🧱 Добавить задачу блокер")
    blocker_team = st.selectbox("На какую команду ставим блокер?", ["(Нет блокера)"] + DEPARTMENTS)
    blocker_name = st.text_input("Название задачи-блокера")
    blocker_desc = st.text_area("Описание требований к блокеру", height=68)
    
    if blocker_team != "(Нет блокера)":
        st.info(f"ℹ️ Оценка (SP) для блокера будет пустой. Команда **{blocker_team}** должна оценить её сама в таблице. Приоритет будет унаследован ({priority}).")

    submitted = st.form_submit_button("Сохранить задачу")

    if submitted:
        if not task_name:
            st.error("Введите название основной задачи!")
        else:
            rows_to_save = []
            
            # Строка основной задачи
            row_main = pd.DataFrame([{
                'Task Name': task_name,
                'Description': description,
                'Requester': main_team,
                'Executor': main_team,
                'Client': client,
                'Priority': priority,
                'Estimate (SP)': estimate,
                'Type': 'Own Task'
            }])
            rows_to_save.append(row_main)
            
            # Строка блокера
            if blocker_team != "(Нет блокера)" and blocker_team != main_team:
                if not blocker_name:
                    st.warning("Название блокера не указано. Блокер не создан.")
                else:
                    row_blocker = pd.DataFrame([{
                        'Task Name': blocker_name,
                        'Description': blocker_desc,
                        'Requester': main_team,
                        'Executor': blocker_team,
                        'Client': client,
                        'Priority': priority,
                        'Estimate (SP)': "",
                        'Type': 'Incoming Blocker'
                    }])
                    rows_to_save.append(row_blocker)

            # Проверка P0 перед сохранением
            if priority == "P0 (Critical)":
                current_df = load_data()
                existing_p0 = current_df[
                    (current_df['Executor'] == main_team) & 
                    (current_df['Priority'] == 'P0 (Critical)') &
                    (current_df['Type'] == 'Own Task')
                ]
                
                if not existing_p0.empty:
                    st.session_state.p0_conflict = True
                    st.session_state.pending_rows = rows_to_save
                    st.rerun() # Перезагрузка для показа диалога
            
            # Сохранение
            save_rows(rows_to_save)
            st.success("Задача сохранена! (Jira-лист обновлен)")
            st.rerun()

# --- АНАЛИТИКА ---
try:
    df_tasks = load_data()
except Exception as e:
    st.error(f"Ошибка загрузки: {e}")
    df_tasks = pd.DataFrame()

if not df_tasks.empty:
    st.divider()
    
    # Преобразуем SP
    df_tasks['Estimate (SP)'] = pd.to_numeric(df_tasks['Estimate (SP)'], errors='coerce').fillna(0)
    
    cap_data = [{'Executor': d, 'Total Capacity': s['people']*s['days']} for d, s in st.session_state.capacity_settings.items()]
    df_cap = pd.DataFrame(cap_data)
    
    usage = df_tasks.groupby(['Executor', 'Type'])['Estimate (SP)'].sum().reset_index()
    
    st.subheader("📊 Загрузка команд (SP)")
    
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=df_cap['Executor'], 
        y=df_cap['Total Capacity'], 
        name='Total Capacity', 
        marker_color='lightgrey'
    ))
    
    for t in ['Own Task', 'Incoming Blocker']:
        sub = usage[usage['Type'] == t]
        if not sub.empty:
            fig.add_trace(go.Bar(
                x=sub['Executor'], 
                y=sub['Estimate (SP)'], 
                name=t,
                text=sub['Estimate (SP)'],
                textposition='auto'
            ))
            
    fig.update_layout(barmode='overlay', title="Capacity vs Workload")
    st.plotly_chart(fig, use_container_width=True)
    
    st.subheader("📋 Список всех задач")
    st.dataframe(df_tasks, use_container_width=True)
