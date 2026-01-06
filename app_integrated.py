"""
생산 관리 통합 시스템
- 탭1: 과거 실적 조회 (8-11월)
- 탭2: 생산 계획 최적화 (1월)
"""

import streamlit as st
from supabase import create_client, Client
import google.generativeai as genai
import requests
import re
import datetime
from datetime import timedelta
import pandas as pd
import json
import plotly.graph_objects as go

# ==================== 환경 설정 ====================
st.set_page_config(page_title="생산 관리 통합 시스템", page_icon="🏭", layout="wide")

# API 설정
try:
    SUPABASE_URL = st.secrets["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
    GEMINI_KEY = st.secrets["GEMINI_API_KEY"]
except:
    SUPABASE_URL = "https://qipphcdzlmqidhrjnjtt.supabase.co"
    SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InFpcHBoY2R6bG1xaWRocmpuanR0Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjY5NTIwMTIsImV4cCI6MjA4MjUyODAxMn0.AsuvjVGCLUJF_IPvQevYASaM6uRF2C6F-CjwC3eCNVk"
    GEMINI_KEY = "AIzaSyBX25WfvCJ-PE0yjjrIBHlM_t9-TdChRgI"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
genai.configure(api_key=GEMINI_KEY)

CAPA_LIMITS = {"조립1": 3300, "조립2": 3700, "조립3": 3600}
FROZEN_DAYS = 3
TEST_MODE = True
TODAY = datetime.datetime(2026, 1, 5).date() if TEST_MODE else datetime.datetime.now().date()

# ==================== 메인 타이틀 ====================
st.title("🏭 생산 관리 통합 시스템")
st.markdown("---")

# ==================== 탭 생성 ====================
tab1, tab2 = st.tabs(["📊 과거 실적 조회 (8-11월)", "🤖 생산 계획 최적화 (1월)"])

# ==================== 탭 1: 과거 실적 조회 ====================
with tab1:
    st.header("📊 8-11월 생산 실적 조회")
    
    # ===== 팀원 코드: 유틸리티 함수 =====
    def extract_date_info(text):
        info = {"date": None, "month": None, "year": "2025"} 
        
        match_date = re.search(r"(\d{1,2})월\s*(\d{1,2})일", text)
        if match_date:
            m, d = match_date.groups()
            info["month"] = int(m)
            info["date"] = f"{info['year']}-{int(m):02d}-{int(d):02d}"
        else:
            match_month = re.search(r"(\d{1,2})월", text)
            if match_month:
                info["month"] = int(match_month.group(1))
        return info

    def extract_version(text):
        if "0차" in text or "초기" in text or "계획" in text:
            return "0차"
        return "최종"

    def extract_product_keyword(text):
        ignore_words = ["생산량", "알려줘", "비교해줘", "비교", "제품", "최종", "0차", "월", "일", "capa", "카파", "초과", "어떻게", "돼", "있어", "사례", "총", 
                        "fan", "motor", "flange", "팬", "모터", "플랜지"]
        words = text.split()
        for w in words:
            clean_w = re.sub(r"[^a-zA-Z0-9가-힣]", "", w)
            if clean_w and clean_w.lower() not in ignore_words and not re.match(r"\d+(월|일)", clean_w):
                return clean_w
        return None

    def normalize_line_name(line_val):
        s = str(line_val).strip()
        if s == '1': return '조립1'
        if s == '2': return '조립2'
        if s == '3': return '조립3'
        if '조립' in s: return s
        return s

    def normalize_date(date_val):
        if not date_val:
            return ""
        s = str(date_val).strip()
        if len(s) >= 10:
            return s[:10]
        return s

    def fetch_db_data(user_input):
        info = extract_date_info(user_input)
        target_date = info["date"]
        target_month = info["month"]
        target_version = extract_version(user_input)
        product_key = extract_product_keyword(user_input)

        context_log = ""
        
        try:
            # 과거 이슈 사례 검색
            if "사례" in user_input:
                issue_mapping = {
                    "MDL1": {"keywords": ["먼저", "줄여", "순위", "교체"], "db_text": "생산순위 조정", "title": "MDL1: 미달(생산순위 조정/모델 교체)"},
                    "MDL2": {"keywords": ["감사", "정지", "설비", "라인전체"], "db_text": "라인전체이슈", "title": "MDL2: 미달(라인전체이슈/설비)"},
                    "MDL3": {"keywords": ["부품", "자재", "결품", "수급", "안되는"], "db_text": "자재결품", "title": "MDL3: 미달(부품수급/자재결품)"},
                    "PRP": {"keywords": ["선행", "미리", "당겨", "땡겨"], "db_text": "선행 생산", "title": "PRP: 선행 생산(숙제 미리하기)"},
                    "SMP": {"keywords": ["샘플", "긴급"], "db_text": "계획외 긴급 생산", "title": "SMP: 계획외 긴급 생산"},
                    "CCL": {"keywords": ["취소"], "db_text": "계획 취소", "title": "CCL: 계획 취소/라인 가동중단"}
                }

                detected_code = None
                for code, meta in issue_mapping.items():
                    if any(k in user_input for k in meta["keywords"]):
                        detected_code = code
                        break
                
                if detected_code:
                    meta = issue_mapping[detected_code]
                    query = supabase.table("production_issue_analysis_8_11").select("품목명, 날짜, 계획_v0, 실적_v2, 누적차이_Gap, 최종_이슈분류")
                    
                    if detected_code == "MDL2":
                        query = query.or_(f"최종_이슈분류.ilike.%라인전체이슈%,최종_이슈분류.ilike.%설비%")
                    elif detected_code == "MDL3":
                        query = query.or_(f"최종_이슈분류.ilike.%부품수급%,최종_이슈분류.ilike.%자재결품%")
                    else:
                        query = query.ilike("최종_이슈분류", f"%{meta['db_text']}%")
                        
                    response = query.limit(3).execute()
                    
                    if response.data:
                        context_log += f"[{detected_code} CASE FOUND]\n"
                        context_log += f"Title: {meta['title']}\n"
                        context_log += f"Data: {json.dumps(response.data, ensure_ascii=False)}"
                        return context_log

            # 월간 생산량 브리핑
            found_months = re.findall(r"(\d{1,2})월", user_input)
            found_months = sorted(list(set([int(m) for m in found_months])))
            
            if len(found_months) >= 2 and product_key is None:
                target_ver = extract_version(user_input)
                res = supabase.table("monthly_production").select("월, 총_생산량").in_("월", found_months).eq("버전", target_ver).execute()
                
                if res.data:
                    df = pd.DataFrame(res.data)
                    df = df.sort_values(by='월')
                    context_log += f"\n[{target_ver} 월간 총 생산량 브리핑]\n"
                    prev_val = None
                    prev_month = None
                    for idx, row in df.iterrows():
                        m = row['월']
                        val = row['총_생산량']
                        msg = f"{m}월: {val:,}"
                        if prev_val is not None:
                            diff = val - prev_val
                            if diff > 0: msg += f" (전월({prev_month}월) 대비 {diff:,} 증가 🔺)"
                            elif diff < 0: msg += f" (전월({prev_month}월) 대비 {abs(diff):,} 감소 🔻)"
                        context_log += f"- {msg}\n"
                        prev_val = val
                        prev_month = m
                    return context_log

            # CAPA 초과 비교
            if ("비교" in user_input and "월" in user_input and product_key is None) or ("초과" in user_input and "월" in user_input):
                res_capa = supabase.table("daily_capa").select("*").eq("월", target_month).eq("버전", "최종").execute()
                res_prod = supabase.table("daily_total_production").select("*").eq("월", target_month).eq("버전", "최종").execute()
                
                if not res_capa.data or not res_prod.data:
                    return "데이터 조회 실패"

                capa_reference = {}
                for item in res_capa.data:
                    line_key = normalize_line_name(item['라인'])
                    capa_reference[line_key] = item['capa']

                over_list = []
                
                for row in res_prod.data:
                    p_date = normalize_date(row['날짜'])
                    p_line = normalize_line_name(row['라인'])
                    p_qty = row['총_생산량']
                    limit = capa_reference.get(p_line, 0)
                    
                    if limit > 0 and p_qty > limit:
                        over_list.append(f"| {p_date} | {p_line} | {limit} | {p_qty} |")
                
                if "초과" in user_input:
                    if over_list:
                        over_list.sort()
                        context_log += f"\n[CAPA 초과 리스트 (형식: 날짜|라인|CAPA|총 생산량)]:\n"
                        for item in over_list:
                            context_log += f"{item}\n"
                    else:
                        context_log += f"\n[알림] {target_month}월 CAPA 초과한 날이 없습니다."
                
                return context_log

        except Exception as e:
            return f"데이터 조회 중 오류 발생: {str(e)}"

        return "요청하신 조건에 맞는 데이터를 찾을 수 없습니다."

    def query_gemini_ai(user_input, context):
        system_prompt = f"""
당신은 숙련된 생산계획 담당자입니다. 제공된 데이터를 기반으로 답하세요.

[Context Data]:
{context}

[User Question]:
{user_input}
"""
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-exp:generateContent?key={GEMINI_KEY}"
        headers = {"Content-Type": "application/json"}
        data = {"contents": [{"parts": [{"text": system_prompt}]}]}
        
        try:
            response = requests.post(url, headers=headers, json=data)
            if response.status_code == 200:
                result = response.json()
                return result['candidates'][0]['content']['parts'][0]['text']
            else:
                return f"API 오류: {response.status_code}"
        except Exception as e:
            return f"통신 오류: {e}"

    # ===== 채팅 UI (과거 조회) =====
    if "messages_tab1" not in st.session_state:
        st.session_state.messages_tab1 = []

    for message in st.session_state.messages_tab1:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    if prompt := st.chat_input("질문을 입력하세요 (예: 9월 CAPA 초과한 날 있어?)", key="tab1_input"):
        st.session_state.messages_tab1.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("데이터 분석 중..."):
                db_result = fetch_db_data(prompt)
                if "찾을 수 없습니다" in db_result or "오류" in db_result:
                    final_response = db_result
                else:
                    final_response = query_gemini_ai(prompt, db_result)
                st.markdown(final_response)
                
        st.session_state.messages_tab1.append({"role": "assistant", "content": final_response})

# ==================== 탭 2: 생산 계획 최적화 ====================
with tab2:
    st.header("🤖 1월 생산 계획 최적화")
    
    # 내 코드 임포트
    from main_engine import ask_professional_scheduler
    from functions_part1 import initialize_globals
    
    # 전역 변수 초기화
    initialize_globals(TODAY, CAPA_LIMITS)
    
    # 데이터 로드 함수
    @st.cache_data(ttl=600)
    def fetch_plan_data(target_date=None):
        try:
            if target_date:
                dt = datetime.datetime.strptime(target_date, '%Y-%m-%d')
                start_date = (dt - timedelta(days=10)).strftime('%Y-%m-%d')
                end_date = (dt + timedelta(days=10)).strftime('%Y-%m-%d')
                plan_res = supabase.table("production_plan_2026_01").select("*").gte("plan_date", start_date).lte("plan_date", end_date).execute()
            else:
                plan_res = supabase.table("production_plan_2026_01").select("*").execute()
            
            plan_df = pd.DataFrame(plan_res.data)
            hist_res = supabase.table("production_investigation").select("*").execute()
            hist_df = pd.DataFrame(hist_res.data)

            if not plan_df.empty:
                plan_df['name_clean'] = plan_df['product_name'].apply(lambda x: re.sub(r'\s+', '', str(x)).strip())
                plt_map = plan_df.groupby('name_clean')['plt'].first().to_dict()
                product_map = plan_df.groupby('name_clean')['line'].unique().to_dict()
                for k in product_map:
                    if "T6" in k.upper(): 
                        product_map[k] = ["조립1", "조립2", "조립3"]
                return plan_df, hist_df, product_map, plt_map
            return pd.DataFrame(), pd.DataFrame(), {}, {}
        except Exception as e:
            st.error(f"데이터 로드 실패: {e}")
            return pd.DataFrame(), pd.DataFrame(), {}, {}
    
    def extract_date_jan(text):
        patterns = [r'(\d{1,2})/(\d{1,2})', r'(\d{1,2})월\s*(\d{1,2})일', r'202[56]-(\d{1,2})-(\d{1,2})']
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                m, d = match.groups()
                return f"2026-{int(m):02d}-{int(d):02d}"
        return None
    
    # 사이드바
    with st.sidebar:
        st.header("⚙️ 1월 최적화 설정")
        st.markdown("### 🔍 수사 방식")
        st.info("""
        **하이브리드 엔진**
        - 🐍 Python: 팩트 수사 (1~4단계)
        - 🤖 AI: 전략 수립 (5단계)
        - 🐍 Python: 최종 검증 (6단계)
        """)
        
        st.markdown("### 📅 기준 정보")
        frozen_date = (datetime.datetime.combine(TODAY, datetime.datetime.min.time()) + timedelta(days=FROZEN_DAYS)).strftime('%Y-%m-%d')
        st.info(f"**기준일**: {TODAY.strftime('%Y-%m-%d')}\n\n**고정 기간**: ~{frozen_date}")
        
        st.markdown("### 🏭 CAPA 한계")
        for line, limit in CAPA_LIMITS.items():
            st.metric(line, f"{limit:,}개")
    
    # 메인 채팅 영역
    if "messages_tab2" not in st.session_state:
        st.session_state.messages_tab2 = []

    for msg in st.session_state.messages_tab2:
        with st.chat_message(msg["role"]): 
            st.markdown(msg["content"])

    if prompt := st.chat_input("질문을 입력하세요 (예: 1/23 조립1 70%만 생산하고 싶어)", key="tab2_input"):
        st.session_state.messages_tab2.append({"role": "user", "content": prompt})
        with st.chat_message("user"): 
            st.markdown(prompt)
        
        target_date = extract_date_jan(prompt)
        
        if not target_date:
            answer = "❌ 날짜를 인식할 수 없습니다. 예: `1/23` 형식으로 입력해주세요."
            st.session_state.messages_tab2.append({"role": "assistant", "content": answer})
            with st.chat_message("assistant"):
                st.markdown(answer)
        else:
            with st.spinner("🔍 하이브리드 수사 진행 중..."):
                plan_df, hist_df, product_map, plt_map = fetch_plan_data(target_date)
                
                if plan_df.empty:
                    answer = "❌ 데이터를 불러올 수 없습니다."
                else:
                    try:
                        initialize_globals(TODAY, CAPA_LIMITS)
                        
                        report, success, charts, status = ask_professional_scheduler(
                            question=prompt,
                            plan_df=plan_df,
                            hist_df=hist_df,
                            product_map=product_map,
                            plt_map=plt_map,
                            question_date=target_date,
                            mode="hybrid"
                        )
                        
                        if success:
                            answer = f"✅ {status}\n\n{report}"
                        else:
                            answer = f"⚠️ {status}\n\n{report}"
                    
                    except Exception as e:
                        answer = f"❌ **오류 발생**\n\n```\n{str(e)}\n```"
                        st.exception(e)
                
                st.session_state.messages_tab2.append({"role": "assistant", "content": answer})
                
                with st.chat_message("assistant"):
                    st.markdown(answer)
                    
                    # CAPA 차트
                    if not plan_df.empty and 'qty_1차' in plan_df.columns:
                        st.markdown("---")
                        st.subheader("📊 CAPA 사용 현황")
                        
                        daily_summary = plan_df.groupby(['plan_date', 'line'])['qty_1차'].sum().reset_index()
                        daily_summary.columns = ['plan_date', 'line', 'current_qty']
                        daily_summary['max_capa'] = daily_summary['line'].map(CAPA_LIMITS)
                        
                        chart_data = daily_summary.pivot(index='plan_date', columns='line', values='current_qty').fillna(0)
                        
                        fig = go.Figure()
                        colors = {'조립1': '#0066CC', '조립2': '#66B2FF', '조립3': '#FF6666'}
                        
                        for line in ['조립1', '조립2', '조립3']:
                            if line in chart_data.columns:
                                fig.add_trace(go.Bar(
                                    name=f'{line}',
                                    x=chart_data.index,
                                    y=chart_data[line],
                                    marker_color=colors[line]
                                ))
                        
                        for line, limit in CAPA_LIMITS.items():
                            fig.add_hline(y=limit, line_dash="dash", line_color=colors[line])
                        
                        fig.update_layout(barmode='group', height=400)
                        st.plotly_chart(fig, use_container_width=True)
