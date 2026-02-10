import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import gspread
import json
from oauth2client.service_account import ServiceAccountCredentials
import os

st.set_page_config(page_title="Quarterly Planning", layout="wide")

# --- Подключение к Google Sheets ---
def get_google_sheet():
    try:
        json_key = os.environ.get("GCP_KEY")
        if not json_key:
            st.error("❌ ОШИБКА: Ключ не найден в Secrets.")
            st.stop()
        
        creds_dict = json.loads(json_key)
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        return client.open("Quarterly Planning Data").sheet1
    except Exception as e:
        st.error(f"Ошибка соединения: {e}")
        st.stop()

def load_data():
    sheet = get_google_sheet()
    
    # ИСПОЛЬЗУЕМ НОВЫЙ МЕТОД ЧТЕНИЯ
    # get_all_values() не ломается от пустых колонок
    raw_data = sheet.get_all_values()
    
    expected_cols = ['Task Name', 'Requester', 'Executor', 'Stream', 'Priority', 'Estimate (MD)', 'Type']
    
    # Если таблица пустая (нет даже заголовков)
    if not raw_data:
        return pd.DataFrame(columns=expected_cols)

    # Берем заголовки из первой строки
    headers = raw_data[0]
    # Берем данные со второй строки
    data = raw_data[1:] if len(raw_data) > 1 else []
    
    # Создаем DataFrame
    df = pd.DataFrame(data, columns=headers)
    
    # Оставляем только нужные нам колонки (отбрасываем пустые хвосты)
    # Если какой-то колонки нет - добавляем
    final_df = pd.DataFrame()
    for col in expected_cols:
        if col in df.columns:
            final_df[col] = df[col]
        else:
            final_df[col] = ""
            
    return final_df

def save_new_row(row_df):
    sheet = get_google_sheet()
    row_list = row_df.values.tolist()[0]
    sheet.append_row(row_list)

# --- Интерфейс ---
st.title("📊 Team Planning Tool")

if st.button("🔄 Обновить данные"):
    st.rerun()

# Загружаем данные (теперь безопасно)
try:
    df_tasks = load_data()
except Exception as e:
    st.error(f"Ошибка данных: {e}")
    df_tasks = pd.DataFrame()

# Константы
DEPARTMENTS = ["Data Platform", "Antifraud", "BI", "Partners"]
STREAMS = ["Betting", "Casino", "CDP"]
PRIORITIES = ["P0 (Critical)", "P1 (High)", "P2 (Medium)", "P3 (Low)"]

if 'capacity_settings' not in st.session_state:
    st.session_state.capacity_settings = {dept: {'people': 5, 'days': 21} for dept in DEPARTMENTS}

# Сайдбар
st.sidebar.header("⚙️ Ресурсы")
for dept in DEPARTMENTS:
    with st.sidebar.expander(f"{dept}", expanded=False):
        p = st.number_input(f"{dept}: Человек", 1, 100, 5, key=f"p_{dept}")
        d = st.number_input(f"{dept}: Дней", 1, 60, 21, key=f"d_{dept}")
        st.session_state.capacity_settings[dept] = {'people': p, 'days': d}

# Форма
st.subheader("➕ Добавить задачу")
with st.form("add_task_form", clear_on_submit=True):
    c1, c2, c3 = st.columns(3)
    with c1:
        task = st.text_input("Название задачи")
        req = st.selectbox("Заказчик", DEPARTMENTS)
    with c2:
        exe = st.selectbox("Исполнитель", DEPARTMENTS)
        stream = st.selectbox("Стрим", STREAMS)
    with c3:
        prio = st.selectbox("Приоритет", PRIORITIES)
        est = st.number_input("Оценка (MD)", 0.1, 100.0, 1.0, step=0.5)

    if st.form_submit_button("Сохранить"):
        if task:
            t_type = "Own Task" if req == exe else "Incoming Blocker"
            new_row = pd.DataFrame([{'Task Name': task, 'Requester': req, 'Executor': exe, 'Stream': stream, 'Priority': prio, 'Estimate (MD)': est, 'Type': t_type}])
            save_new_row(new_row)
            st.success("Сохранено!")
            st.rerun()

# Графики
if not df_tasks.empty:
    st.divider()
    
    # Превращаем текст в числа (на всякий случай)
    df_tasks['Estimate (MD)'] = pd.to_numeric(df_tasks['Estimate (MD)'], errors='coerce').fillna(0)
    
    cap_data = [{'Executor': d, 'Total Capacity': s['people']*s['days']} for d, s in st.session_state.capacity_settings.items()]
    df_cap = pd.DataFrame(cap_data)
    
    usage = df_tasks.groupby(['Executor', 'Type'])['Estimate (MD)'].sum().reset_index()
    
    st.subheader("Загрузка vs Капасити")
    fig = go.Figure()
    fig.add_trace(go.Bar(x=df_cap['Executor'], y=df_cap['Total Capacity'], name='Max Limit', marker_color='lightgrey'))
    for t in ['Own Task', 'Incoming Blocker']:
        sub = usage[usage['Type'] == t]
        if not sub.empty:
            fig.add_trace(go.Bar(x=sub['Executor'], y=sub['Estimate (MD)'], name=t))
    fig.update_layout(barmode='overlay')
    st.plotly_chart(fig, use_container_width=True)
    
    st.subheader("Таблица задач")
    st.dataframe(df_tasks)
