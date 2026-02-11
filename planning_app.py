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
    
    # Обновленные заголовки (Client вместо Stream)
    expected_cols = ['Task Name', 'Description', 'Requester', 'Executor', 'Client', 'Priority', 'Estimate (SP)', 'Type']
    
    if not raw_data:
        sheet.append_row(expected_cols)
        return pd.DataFrame(columns=expected_cols)

    # Обновление шапки, если она старая (или поменялась структура)
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

# --- ИНТЕРФЕЙС ---
st.title("📊 Quarterly Planning Tool")

if st.button("🔄 Обновить данные"):
    st.rerun()

# --- КОНСТАНТЫ ---
DEPARTMENTS = ["Data Platform", "BI", "ML", "DA", "DE", "Data Ops", "WAS"]

# Новый список Заказчиков (бывшие Стримы)
CLIENTS = ["Data Department", "Partners", "Global Admin Panel", "Betting", "Casino", "Finance Core"]

PRIORITIES = ["P0 (Critical)", "P1 (High)", "P2 (Medium)", "P3 (Low)"]
SP_OPTIONS = [1, 2, 3, 5, 8]

# Настройки капасити
if 'capacity_settings' not in st.session_state:
    st.session_state.capacity_settings = {dept: {'people': 5, 'days': 21} for dept in DEPARTMENTS}

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
    # 1. Чья задача
    main_team = st.selectbox("Чья задача? (Кто исполнитель)", DEPARTMENTS)
    
    task_name = st.text_input("Название задачи", placeholder="Краткая суть...")
    description = st.text_area("Описание задачи", placeholder="Детали, DoD...", height=100)
    
    col_client, col_prio, col_sp = st.columns(3)
    with col_client:
        # Теперь здесь выбираем Заказчика (ex-Stream)
        client = st.selectbox("Заказчик", CLIENTS)
    with col_prio:
        priority = st.selectbox("Приоритет", PRIORITIES)
    with col_sp:
        estimate = st.select_slider("Оценка в SP (Своей задачи)", options=SP_OPTIONS, value=1)

    st.markdown("---")
    
    # --- БЛОКЕР ---
    st.markdown("### 🧱 Добавить задачу блокер")
    st.caption("Если вы зависите от другой команды, выберите её ниже.")
    
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
            
            # 1. ОСНОВНАЯ ЗАДАЧА
            row_main = pd.DataFrame([{
                'Task Name': task_name,
                'Description': description,
                'Requester': main_team,
                'Executor': main_team,
                'Client': client,       # Используем переменную client
                'Priority': priority,
                'Estimate (SP)': estimate,
                'Type': 'Own Task'
            }])
            rows_to_save.append(row_main)
            
            # 2. БЛОКЕР (Если есть)
            if blocker_team != "(Нет блокера)" and blocker_team != main_team:
                if not blocker_name:
                    st.warning("Название блокера не заполнено, он не будет создан.")
                else:
                    row_blocker = pd.DataFrame([{
                        'Task Name': blocker_name,
                        'Description': blocker_desc,
                        'Requester': main_team,     # Заказчик - текущая команда
                        'Executor': blocker_team,   # Исполнитель - кого выбрали
                        'Client': client,           # Тот же клиент
                        'Priority': priority,       # Тот же приоритет
                        'Estimate (SP)': "",        # Оценка пустая
                        'Type': 'Incoming Blocker'
                    }])
                    rows_to_save.append(row_blocker)
                    st.success(f"Создан блокер на команду {blocker_team}")

            save_rows(rows_to_save)
            st.success("Основная задача сохранена!")
            st.rerun()

# --- АНАЛИТИКА ---
try:
    df_tasks = load_data()
except Exception as e:
    st.error(f"Ошибка загрузки: {e}")
    df_tasks = pd.DataFrame()

if not df_tasks.empty:
    st.divider()
    
    # Превращаем SP в числа
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
