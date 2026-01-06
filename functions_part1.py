"""
생산 계획 하이브리드 시스템 - 핵심 함수 Part 1
1~3단계: 품목 나열, 누적 납기, 목적지 CAPA
"""

import pandas as pd
from datetime import datetime, timedelta

# 전역 변수 (app.py에서 초기화)
TODAY = None
CAPA_LIMITS = None

def initialize_globals(today, capa_limits):
    """전역 변수 초기화"""
    global TODAY, CAPA_LIMITS
    TODAY = today
    CAPA_LIMITS = capa_limits

# ==================== 유틸리티 함수 ====================
def get_workdays_from_db(plan_df, start_date_str, direction='future', days_count=10):
    """DB의 is_workday 컬럼 기반 가동일 리스트 반환"""
    if plan_df.empty or 'is_workday' not in plan_df.columns:
        return []

    db_dates = plan_df[['plan_date', 'is_workday']].drop_duplicates().sort_values('plan_date')
    
    if direction == 'future':
        available = db_dates[(db_dates['plan_date'] >= start_date_str) & (db_dates['is_workday'] == True)]
        return available['plan_date'].head(days_count).tolist()
    else:
        available = db_dates[(db_dates['plan_date'] < start_date_str) & 
                             (db_dates['plan_date'] > TODAY.strftime('%Y-%m-%d')) & 
                             (db_dates['is_workday'] == True)]
        return available['plan_date'].tail(days_count).tolist()

def is_workday_in_db(plan_df, date_str):
    """특정 날짜가 가동일인지 확인"""
    if plan_df.empty or 'is_workday' not in plan_df.columns:
        return False
    
    date_info = plan_df[plan_df['plan_date'] == date_str]
    if not date_info.empty:
        return date_info.iloc[0]['is_workday']
    return False

# ==================== [1단계] 품목/수량 나열 ====================
def step1_list_current_stock(plan_df, target_date, target_line):
    """
    1단계: target_date의 qty_1차 품목과 수량 나열
    
    Returns:
        dict: {date, line, total, items: [{name, qty_1차, plt}]}
        str: 에러 메시지 (성공 시 None)
    """
    
    current_stocks = plan_df[(plan_df['plan_date'] == target_date) & 
                             (plan_df['line'] == target_line)].copy()
    
    if current_stocks.empty:
        return None, "해당 날짜에 생산 계획이 없습니다."
    
    current_total = int(current_stocks['qty_1차'].sum())
    
    items = []
    for _, row in current_stocks.iterrows():
        if int(row['qty_1차']) > 0:
            items.append({
                'name': row['product_name'],
                'qty_1차': int(row['qty_1차']),
                'plt': int(row['plt'])
            })
    
    return {
        'date': target_date,
        'line': target_line,
        'total': current_total,
        'items': items
    }, None

# ==================== [2단계] 누적 납기 여유 계산 (개선) ====================
def step2_calculate_cumulative_slack(plan_df, stock_result):
    """
    2단계: 각 품목의 누적 납기 여유 계산 (개선 버전)
    
    핵심 로직:
    - 기존: 누적 생산량(cumsum_1차) - 누적 납기량(cumsum_0차) = 이동 가능 최대 수량
    - 개선: 누적 여유가 0이어도, 미래 생산/납기를 고려하여 당일 생산량 일부 이동 가능
    
    Returns:
        list: [{name, qty_1차, plt, cumsum_target, cumsum_actual, max_movable, 
                last_due, buffer_days, movable}]
    """
    
    items_with_slack = []
    
    for item in stock_result['items']:
        p_name = item['name']
        
        # 품목의 전체 시계열 데이터
        p_series = plan_df[plan_df['product_name'] == p_name].sort_values('plan_date').copy()
        
        if p_series.empty:
            continue
        
        # 누적 계산
        p_series['cumsum_0차'] = p_series['qty_0차'].cumsum()
        p_series['cumsum_1차'] = p_series['qty_1차'].cumsum()
        
        # target_date 시점 데이터
        today_data = p_series[p_series['plan_date'] == stock_result['date']]
        
        if today_data.empty:
            continue
        
        today_data = today_data.iloc[0]
        
        cumsum_target = int(today_data['cumsum_0차'])
        cumsum_actual = int(today_data['cumsum_1차'])
        max_movable_cumsum = cumsum_actual - cumsum_target  # 기존 누적 여유
        
        # ⭐ 새로운 로직: 누적 여유가 0이어도 당일 생산량의 일부는 이동 가능
        # 단, 미래 납기를 고려해야 함
        
        # 미래 납기 확인
        future_demand = p_series[p_series['plan_date'] > stock_result['date']]['qty_0차'].sum()
        future_production = p_series[p_series['plan_date'] > stock_result['date']]['qty_1차'].sum()
        
        # 미래 생산 - 미래 납기 = 미래 여유
        future_slack = future_production - future_demand
        
        # 최종 이동 가능 수량 계산
        if max_movable_cumsum > 0:
            # 기존 로직: 누적 여유가 있으면 그대로 사용
            max_movable = max_movable_cumsum
        else:
            # 새로운 로직: 누적 여유 0이어도 당일 생산량 일부 이동 가능
            if future_slack >= 0:
                # 미래에 여유가 있으면 당일 전체 이동 가능
                max_movable = item['qty_1차']
            else:
                # 미래에 부족하면 그 부족분만큼 빼고 이동
                max_movable = max(0, item['qty_1차'] + future_slack)
        
        # 납기 정보
        due_dates = p_series[p_series['qty_0차'] > 0]['plan_date'].tolist()
        last_due = max(due_dates) if due_dates else "미확인"
        
        if last_due != "미확인":
            last_due_dt = datetime.strptime(last_due, '%Y-%m-%d').date()
            target_date_dt = datetime.strptime(stock_result['date'], '%Y-%m-%d').date()
            buffer_days = (last_due_dt - target_date_dt).days
        else:
            buffer_days = 999
        
        items_with_slack.append({
            'name': p_name,
            'qty_1차': item['qty_1차'],
            'plt': item['plt'],
            'cumsum_target': cumsum_target,
            'cumsum_actual': cumsum_actual,
            'max_movable': max_movable,
            'last_due': last_due,
            'buffer_days': buffer_days,
            'movable': max_movable >= item['plt']  # 1PLT 이상 여유
        })
    
    return items_with_slack

# ==================== [3단계] 목적지 CAPA 현황 분석 ====================
def step3_analyze_destination_capacity(plan_df, target_date, target_line):
    """
    3단계: 목적지 CAPA 현황 분석 (병목 전이 방지)
    
    분석 대상:
    1. 같은 날짜 타라인 (이송용)
    2. 미래 날짜 동일라인 (연기용)
    
    Returns:
        dict: {"{date}_{line}": {date, line, current, remaining, max, usage_rate}}
    """
    
    future_workdays = get_workdays_from_db(plan_df, target_date, direction='future', days_count=10)
    
    capa_status = {}
    
    for line in ["조립1", "조립2", "조립3"]:
        # 같은 날짜 타라인 (이송 목적지)
        if line != target_line:
            current = plan_df[(plan_df['plan_date'] == target_date) & 
                            (plan_df['line'] == line)]['qty_1차'].sum()
            remaining = CAPA_LIMITS[line] - current
            capa_status[f"{target_date}_{line}"] = {
                'date': target_date,
                'line': line,
                'current': int(current),
                'remaining': int(remaining),
                'max': CAPA_LIMITS[line],
                'usage_rate': (current / CAPA_LIMITS[line] * 100) if CAPA_LIMITS[line] > 0 else 0
            }
        
        # 미래 날짜 동일라인 (연기 목적지)
        if line == target_line:
            # future_workdays가 비어있으면 수동 생성
            if not future_workdays:
                target_dt = datetime.strptime(target_date, '%Y-%m-%d')
                for i in range(1, 11):
                    future_date = (target_dt + timedelta(days=i)).strftime('%Y-%m-%d')
                    if is_workday_in_db(plan_df, future_date):
                        future_workdays.append(future_date)
            
            for date in future_workdays:
                current = plan_df[(plan_df['plan_date'] == date) & 
                                (plan_df['line'] == line)]['qty_1차'].sum()
                remaining = CAPA_LIMITS[line] - current
                capa_status[f"{date}_{line}"] = {
                    'date': date,
                    'line': line,
                    'current': int(current),
                    'remaining': int(remaining),
                    'max': CAPA_LIMITS[line],
                    'usage_rate': (current / CAPA_LIMITS[line] * 100) if CAPA_LIMITS[line] > 0 else 0
                }
    
    return capa_status
