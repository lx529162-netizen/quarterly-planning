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
    
    # Новые заголовки (добавили Description и изменили MD на SP)
    expected_cols = ['Task Name', 'Description', 'Requester', 'Executor', 'Stream', 'Priority', 'Estimate (SP)', 'Type']
    
    if not raw_data:
        # Если пусто - создаем шапку
        sheet.append_row(expected_cols)
        return pd.DataFrame(columns=expected_cols)

    # Проверка и обновление шапки, если старая
    if raw_data[0] != expected_cols:
        # Если шапка отличается (например, старая версия), обновляем первую строку
        sheet.update(range_name='A1:H1', values=[expected_cols])
        raw_data = sheet.get_all_values() # Перечитываем

    headers = raw_data[0]
    data = raw_data[1:] if len(raw_data) > 1 else []
    
    df = pd.DataFrame(data, columns=headers)
    return df

# --- СОХРАНЕНИЕ (Умеет сохранять сразу несколько строк) ---
def save_rows(rows_list):
    sheet = get_google_sheet()
    # Превращаем DataFrame-строки в списки
    values_to_append = []
    for row_df in rows_list:
        values_to_append.append(row_df.values.tolist()[0])
    
    # Отправляем всё пачкой (быстрее и надежнее)
    sheet.append_rows(values_to_append)

# --- ИНТЕРФЕЙС ---
st.title("📊 Quarterly Planning Tool")

if st.button("🔄 Обновить данные"):
    st.rerun()

# Константы
DEPARTMENTS = ["Data Platform", "Antifraud", "BI", "Partners"]
STREAMS = ["Betting", "Casino", "CDP"]
PRIORITIES = ["P0 (Critical)", "P1 (High)", "P2 (Medium)", "P3 (Low)"]
SP_OPTIONS = [1, 2, 3, 5, 8] # Только числа Фибоначчи

# Настройки капасити
if 'capacity_settings' not in st.session_state:
    st.session_state.capacity_settings = {dept: {'people': 5, 'days': 21} for dept in DEPARTMENTS}

# --- САЙДБАР (Настройки) ---
st.sidebar.header("⚙️ Ресурсы команд")
st.sidebar.info("1 SP = 1 Человеко-день")
for dept in DEPARTMENTS:
    with st.sidebar.expander(f"{dept}", expanded=False):
        p = st.number_input(f"{dept}: Человек", 1, 100, 5, key=f"p_{dept}")
        d = st.number_input(f"{dept}: Дней", 1, 60, 21, key=f"d_{dept}")
        st.session_state.capacity_settings[dept] = {'people': p, 'days': d}

# --- ОСНОВНАЯ ФОРМА ---
st.subheader("➕ Создание задачи")

with st.form("main_form", clear_on_submit=True):
    # 1. Главный вопрос: Чья задача?
    main_team = st.selectbox("Чья задача? Какая команда ее будет делать?", DEPARTMENTS)
    
    # 2. Название и Описание (На всю ширину)
    task_name = st.text_input("Название задачи", placeholder="Краткая суть задачи...")
    description = st.text_area("Описание задачи", placeholder="Детали реализации, DoD...", height=100)
    
    col_str, col_prio, col_sp = st.columns(3)
    with col_str:
        stream = st.selectbox("Стрим", STREAMS)
    with col_prio:
        priority = st.selectbox("Приоритет", PRIORITIES)
    with col_sp:
        # Слайдер для SP (1, 2, 3, 5, 8)
        estimate = st.select_slider("Оценка в SP", options=SP_OPTIONS, value=1)

    st.markdown("---")
    
    # --- СЕКЦИЯ БЛОКЕРА ---
    st.markdown("### 🧱 Добавить задачу блокер на другую команду")
    st.caption("Если для выполнения вашей задачи нужна помощь другой команды, заполните поля ниже.")
    
    blocker_team = st.selectbox("Выбери команду (на кого ставим блокер)", ["(Нет блокера)"] + DEPARTMENTS)
    
    # Показываем поля блокера, только визуально они всегда есть, но логика сработает при выборе команды
    b_col1, b_col2 = st.columns([1, 1])
    with b_col1:
        blocker_name = st.text_input("Название задачи-блокера")
    with b_col2:
        # Блокеру тоже нужна оценка, по умолчанию ставим 1, чтобы график рисовался
        blocker_sp = st.select_slider("Оценка блокера (SP)", options=SP_OPTIONS, value=1, key="blk_sp")
        
    blocker_desc = st.text_area("Описание задачи-блокера", height=68)

    submitted = st.form_submit_button("Сохранить задачу (и блокер, если есть)")

    if submitted:
        if not task_name:
            st.error("Введите название основной задачи!")
        else:
            rows_to_save = []
            
            # 1. Формируем ОСНОВНУЮ задачу
            # Requester = Main Team, Executor = Main Team -> Own Task
            row_main = pd.DataFrame([{
                'Task Name': task_name,
                'Description': description,
                'Requester': main_team,
                'Executor': main_team,
                'Stream': stream,
                'Priority': priority,
                'Estimate (SP)': estimate,
                'Type': 'Own Task'
            }])
            rows_to_save.append(row_main)
            
            # 2. Формируем БЛОКЕР (если выбран)
            if blocker_team != "(Нет блокера)" and blocker_team != main_team:
                if not blocker_name:
                    st.warning("Выбрана команда для блокера, но не указано название задачи. Блокер не создан.")
                else:
                    # Requester = Main Team (кто просит), Executor = Blocker Team (кто делает) -> Incoming Blocker
                    row_blocker = pd.DataFrame([{
                        'Task Name': blocker_name,
                        'Description': blocker_desc,
                        'Requester': main_team,     # Просит тот, кто заполняет форму
                        'Executor': blocker_team,   # Делает тот, кого выбрали
                        'Stream': stream,           # Стрим наследуем
                        'Priority': priority,       # Приоритет наследуем
                        'Estimate (SP)': blocker_sp,
                        'Type': 'Incoming Blocker'
                    }])
                    rows_to_save.append(row_blocker)
                    st.info(f"Дополнительно создан блокер на команду {blocker_team}")

            # Сохраняем всё разом
            save_rows(rows_to_save)
            st.success("Основная задача сохранена!")
            st.rerun()

# --- АНАЛИТИКА И ГРАФИКИ ---
try:
    df_tasks = load_data()
except Exception as e:
    st.error(f"Ошибка загрузки: {e}")
    df_tasks = pd.DataFrame()

if not df_tasks.empty:
    st.divider()
    
    # Преобразуем SP в числа
    df_tasks['Estimate (SP)'] = pd.to_numeric(df_tasks['Estimate (SP)'], errors='coerce').fillna(0)
    
    # Считаем капасити (1 чел * 1 день = 1 SP)
    cap_data = [{'Executor': d, 'Total Capacity': s['people']*s['days']} for d, s in st.session_state.capacity_settings.items()]
    df_cap = pd.DataFrame(cap_data)
    
    # Группируем факт
    usage = df_tasks.groupby(['Executor', 'Type'])['Estimate (SP)'].sum().reset_index()
    
    st.subheader("📊 Загрузка команд (SP)")
    
    fig = go.Figure()
    # 1. Серая подложка - Общее Капасити
    fig.add_trace(go.Bar(
        x=df_cap['Executor'], 
        y=df_cap['Total Capacity'], 
        name='Total Capacity', 
        marker_color='lightgrey',
        text=df_cap['Total Capacity'],
        textposition='auto'
    ))
    
    # 2. Цветные бары - Задачи
    for t in ['Own Task', 'Incoming Blocker']:
        sub = usage[usage['Type'] == t]
        if not sub.empty:
            fig.add_trace(go.Bar(
                x=sub['Executor'], 
                y=sub['Estimate (SP)'], 
                name=t,
                text=sub['Estimate (SP)'],
                textposition='inside'
            ))
            
    fig.update_layout(barmode='overlay', title="Capacity (Grey) vs Planned Work (Colored)")
    st.plotly_chart(fig, use_container_width=True)
    
    st.subheader("📋 Список всех задач")
    # Показываем таблицу, скрывая технические поля если нужно
    st.dataframe(df_tasks, use_container_width=True)
