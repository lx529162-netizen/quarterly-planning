import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# 1. Настройка страницы
st.set_page_config(page_title="Quarterly Planning", layout="wide")

# --- ПОДКЛЮЧЕНИЕ К GOOGLE SHEETS ---
def get_google_sheet():
    try:
        if "gcp_service_account" not in st.secrets:
            st.error("❌ Не найден раздел [gcp_service_account] в Secrets.")
            st.stop()
            
        creds_dict = dict(st.secrets["gcp_service_account"])
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        return client.open("Quarterly Planning Data").sheet1
    except Exception as e:
        st.error(f"❌ Ошибка подключения: {e}")
        st.stop()

# --- ЧТЕНИЕ ДАННЫХ ---
def load_data():
    sheet = get_google_sheet()
    raw_data = sheet.get_all_values()
    
    expected_cols = ['Task Name', 'Description', 'Requester', 'Executor', 'Client', 'Priority', 'Estimate (SP)', 'Type']
    
    if not raw_data:
        sheet.append_row(expected_cols)
        return pd.DataFrame(columns=expected_cols)

    if raw_data[0] != expected_cols:
        sheet.update(range_name='A1:H1', values=[expected_cols])
        raw_data = sheet.get_all_values()

    headers = raw_data[0]
    data = raw_data[1:] if len(raw_data) > 1 else []
    
    df = pd.DataFrame(data, columns=headers)
    return df

# --- СОХРАНЕНИЕ ---
def save_rows(rows_list):
    sheet = get_google_sheet()
    values_to_append = []
    for row_df in rows_list:
        values_to_append.append(row_df.values.tolist()[0])
    sheet.append_rows(values_to_append)

# --- НОВАЯ ФУНКЦИЯ: ПОНИЖЕНИЕ ПРИОРИТЕТА ---
def downgrade_existing_p0(executor_team):
    sheet = get_google_sheet()
    # Читаем все данные, чтобы найти нужную строку
    all_values = sheet.get_all_values()
    
    # Перебираем строки (начиная со 2-й, т.к. 1-я это заголовки)
    # Индекс i в enumerate будет 0 для первой строки данных (которая в таблице строка №2)
    # Нам нужно найти строку, где Executor == executor_team И Priority == P0 (Critical) И Type == Own Task
    
    # Колонки (индексы начинаются с 0):
    # 0: Task Name, 1: Desc, 2: Req, 3: Exec, 4: Client, 5: Priority, 6: SP, 7: Type
    
    for i, row in enumerate(all_values):
        if i == 0: continue # Пропускаем заголовок
        
        # Проверяем условия
        if (len(row) > 7 and 
            row[3] == executor_team and 
            row[5] == "P0 (Critical)" and 
            row[7] == "Own Task"):
            
            # Нашли! Строка в Google Sheets = i + 1 (так как нумерация с 1)
            row_number = i + 1
            
            # Обновляем ячейку Приоритета (Колонка F = 6)
            sheet.update_cell(row_number, 6, "P1 (High)")
            return True # Успешно понизили
            
    return False # Не нашли (на всякий случай)

# --- ИНТЕРФЕЙС ---
st.title("📊 Quarterly Planning Tool")

if st.button("🔄 Обновить данные"):
    st.rerun()

# --- КОНСТАНТЫ ---
DEPARTMENTS = ["Data Platform", "BI", "ML", "DA", "DE", "Data Ops", "WAS"]
CLIENTS = ["Data Department", "Partners", "Global Admin Panel", "Betting", "Casino", "Finance Core"]
PRIORITIES = ["P0 (Critical)", "P1 (High)", "P2 (Medium)", "P3 (Low)"]
SP_OPTIONS = [1, 2, 3, 5, 8]

if 'capacity_settings' not in st.session_state:
    st.session_state.capacity_settings = {dept: {'people': 5, 'days': 21} for dept in DEPARTMENTS}

# --- ИНИЦИАЛИЗАЦИЯ СОСТОЯНИЯ КОНФЛИКТА ---
if 'p0_conflict' not in st.session_state:
    st.session_state.p0_conflict = False
    st.session_state.pending_rows = [] # Здесь будем хранить задачу, пока юзер думает

# ==========================================
# БЛОК РАЗРЕШЕНИЯ КОНФЛИКТА (Появляется при P0)
# ==========================================
if st.session_state.p0_conflict:
    st.warning(f"⚠️ **Внимание!** У команды уже есть задача с приоритетом P0 (Critical).")
    st.write("Может быть только 1 крит в плане.")
    st.write("**Понизить приоритет СУЩЕСТВУЮЩЕГО крита до P1 (High)?**")
    
    col_yes, col_no = st.columns(2)
    
    with col_yes:
        if st.button("ДА, понизить старый до P1, новый записать как P0"):
            # 1. Понижаем старый в таблице
            executor = st.session_state.pending_rows[0]['Executor'].iloc[0]
            downgrade_existing_p0(executor)
            
            # 2. Сохраняем новый как есть (он уже P0)
            save_rows(st.session_state.pending_rows)
            
            st.success("Готово! Старый крит стал P1, новый записан как P0.")
            # Сброс состояния
            st.session_state.p0_conflict = False
            st.session_state.pending_rows = []
            st.rerun()

    with col_no:
        if st.button("НЕТ, не трогать старый, новый записать как P1"):
            # 1. Берем новую задачу и насильно меняем ей приоритет на P1
            rows = st.session_state.pending_rows
            # Меняем приоритет у основной задачи (она первая в списке)
            rows[0]['Priority'] = "P1 (High)"
            
            # Если есть блокер, ему тоже меняем (он второй в списке)
            if len(rows) > 1:
                rows[1]['Priority'] = "P1 (High)"
            
            # 2. Сохраняем
            save_rows(rows)
            
            st.success("Готово! Старый крит остался, новая задача сохранена как P1.")
            # Сброс состояния
            st.session_state.p0_conflict = False
            st.session_state.pending_rows = []
            st.rerun()
            
    st.markdown("---") 
    # Останавливаем выполнение, чтобы не рисовать форму снизу, пока не решат конфликт
    st.stop() 


# --- САЙДБАР ---
st.sidebar.header("⚙️ Ресурсы команд")
st.sidebar.info("1 SP = 1 Человеко-день")
for dept in DEPARTMENTS:
    with st.sidebar.expander(f"{dept}", expanded=False):
        p = st.number_input(f"{dept}: Человек", 1, 100, 5, key=f"p_{dept}")
        d = st.number_input(f"{dept}: Дней", 1, 60, 21, key=f"d_{dept}")
        st.session_state.capacity_settings[dept] = {'people': p, 'days': d}

# --- ФОРМА ---
st.subheader("➕ Создание задачи")

with st.form("main_form", clear_on_submit=True):
    main_team = st.selectbox("Чья задача? (Кто исполнитель)", DEPARTMENTS)
    
    task_name = st.text_input("Название задачи", placeholder="Краткая суть...")
    description = st.text_area("Описание задачи", placeholder="Детали, DoD...", height=100)
    
    col_client, col_prio, col_sp = st.columns(3)
    with col_client:
        client = st.selectbox("Заказчик", CLIENTS)
    with col_prio:
        priority = st.selectbox("Приоритет", PRIORITIES)
    with col_sp:
        estimate = st.select_slider("Оценка в SP (Своей задачи)", options=SP_OPTIONS, value=1)

    st.markdown("---")
    
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
            # Подготовка данных (но пока НЕ сохранение)
            rows_to_save = []
            
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
            
            if blocker_team != "(Нет блокера)" and blocker_team != main_team:
                if not blocker_name:
                    st.warning("Блокер не будет создан: нет названия.")
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

            # --- ЛОГИКА ПРОВЕРКИ P0 ---
            # Проверяем только если пытаемся создать P0
            if priority == "P0 (Critical)":
                # Загружаем текущие данные для проверки
                current_df = load_data()
                
                # Ищем, есть ли у ЭТОГО исполнителя (main_team) уже P0 задача типа Own Task
                existing_p0 = current_df[
                    (current_df['Executor'] == main_team) & 
                    (current_df['Priority'] == 'P0 (Critical)') &
                    (current_df['Type'] == 'Own Task')
                ]
                
                if not existing_p0.empty:
                    # КОНФЛИКТ!
                    st.session_state.p0_conflict = True
                    st.session_state.pending_rows = rows_to_save
                    st.rerun() # Перезагружаем страницу, чтобы показать блок с кнопками Да/Нет
            
            # Если конфликта нет (или приоритет не P0), сохраняем сразу
            save_rows(rows_to_save)
            st.success("Задача сохранена!")
            st.rerun()

# --- АНАЛИТИКА ---
try:
    df_tasks = load_data()
except Exception as e:
    st.error(f"Ошибка загрузки: {e}")
    df_tasks = pd.DataFrame()

if not df_tasks.empty:
    st.divider()
    
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
